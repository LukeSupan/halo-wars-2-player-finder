import sys
import time
from collections import defaultdict
from dataclasses import dataclass

from .config import (
    API_KEY,
    BASE_URL,
    END_DATE,
    FORMATTED_OUTPUT_FILE,
    HEADERS,
    INCLUDE_BOT_GAMES,
    MATCH_HISTORY_OUTPUT_FILE,
    MATCH_TYPES,
    MIN_MATCH_DURATION_SECONDS,
    MIN_TRACKED_PLAYERS,
    RAW_EXPORT_FILE,
    REQUEST_DELAY,
    START_DATE,
    STATS_OUTPUT_FILE,
)
from .formatting import (
    OutputFiles,
    add_player_stats,
    display_name_for_player,
    format_match_line,
    has_winners_and_losers,
    match_history_block,
)
from .halo_api import (
    HaloApiClient,
    fetch_match_details,
    load_match_details_cache,
    save_match_details_cache,
)
from .io_files import load_player_aliases, load_tracked_players, write_output_files
from .match_rules import (
    bot_count,
    dedupe_participants,
    find_date_field,
    has_human_on_each_team,
    has_only_tracked_humans,
    has_only_tracked_players,
    is_custom_match,
    is_long_enough_match,
    leader_label,
    map_label,
    needs_match_detail_verification,
    parse_date,
    tracked_participants_from_details,
)


@dataclass
class MatchRunStats:
    """Counters printed at the end of a run."""

    fast_path_matches: int = 0
    detail_checked_matches: int = 0
    verified_suspicious_matches: int = 0
    included_bot_matches: int = 0
    skipped_missing_details: int = 0
    skipped_unlisted_players: int = 0
    skipped_bot_team_without_human: int = 0
    skipped_same_side: int = 0


def ensure_api_key_is_configured():
    if API_KEY and API_KEY != "PASTE_YOUR_SUBSCRIPTION_KEY_HERE":
        return

    print("Set your API key first: copy .env.example to .env, set")
    print("HALO_API_KEY, then re-run.")
    sys.exit(1)


def collect_match_history(api_client, tracked_players, start_date, end_date):
    """Fetch each tracked player's history and group rows by MatchId."""
    matches_by_id = defaultdict(list)  # match_id -> [(player, history_row), ...]

    for player in tracked_players:
        for match_type in MATCH_TYPES:
            print(f"Fetching {match_type} history for {player}...")
            history_entries = api_client.fetch_player_history(
                player,
                match_type,
                start_date,
                end_date,
            )
            print(f"  {len(history_entries)} {match_type} matches found")

            for entry in history_entries:
                match_id = entry.get("MatchId") or entry.get("Id")
                if not match_id:
                    continue
                matches_by_id[match_id].append((player, entry))

            time.sleep(REQUEST_DELAY)

    return matches_by_id


def qualifying_matches(matches_by_id):
    """Keep only custom matches with enough tracked players and duration."""
    return {
        match_id: participants
        for match_id, participants in matches_by_id.items()
        if (
            len(participants) >= MIN_TRACKED_PLAYERS
            and is_custom_match(participants)
            and is_long_enough_match(participants)
        )
    }


def print_search_summary(matches_by_id, qualifying, start_date, end_date, include_bot_games):
    print(f"\n{len(matches_by_id)} total unique matches seen across tracked players.")
    if start_date:
        print(f"Only checked matches on or after {START_DATE}.")
    if end_date:
        print(f"Only checked matches on or before {END_DATE}.")
    if include_bot_games:
        print("Slow bot-game mode enabled: checking full details for extra-player matches.")
    print(
        f"{len(qualifying)} custom matches lasted at least "
        f"{MIN_MATCH_DURATION_SECONDS} seconds "
        f"and had {MIN_TRACKED_PLAYERS}+ tracked players.\n"
    )
    print("Formatted matches:")
    print("=" * 70)


def match_date_sort_key(match_item):
    _, participants = match_item
    return str(find_date_field(participants[0][1]) or "")


def verified_participants_for_match(
    api_client,
    match_id,
    history_participants,
    include_bot_games,
    tracked_players,
    match_details_cache,
    run_stats,
):
    """Return trusted participants for one match, or None when it should skip."""
    has_extra_players = not has_only_tracked_players(history_participants)
    needs_full_details = needs_match_detail_verification(history_participants)

    if has_extra_players and not include_bot_games:
        run_stats.skipped_unlisted_players += 1
        return None

    # Extra players might be bots, but history rows cannot prove that.
    if has_extra_players:
        needs_full_details = True

    if not needs_full_details:
        run_stats.fast_path_matches += 1
        return history_participants, 0

    match_details, loaded_from_cache = fetch_match_details(
        api_client,
        match_id,
        match_details_cache,
    )
    if not loaded_from_cache:
        save_match_details_cache(match_details_cache)
        time.sleep(REQUEST_DELAY)

    if not match_details:
        run_stats.skipped_missing_details += 1
        return None

    run_stats.detail_checked_matches += 1

    if not has_only_tracked_humans(match_details, tracked_players):
        run_stats.skipped_unlisted_players += 1
        return None

    bot_total = bot_count(match_details)
    if has_extra_players:
        if bot_total == 0:
            run_stats.skipped_unlisted_players += 1
            return None
        if not has_human_on_each_team(match_details):
            run_stats.skipped_bot_team_without_human += 1
            return None

    participants = tracked_participants_from_details(match_details, tracked_players)
    if (
        len(participants) < MIN_TRACKED_PLAYERS
        or not is_custom_match(participants)
        or not is_long_enough_match(participants)
    ):
        return None

    if needs_match_detail_verification(history_participants):
        run_stats.verified_suspicious_matches += 1

    return participants, bot_total


def add_match_to_outputs(
    output_files,
    player_stats,
    match_id,
    participants,
    bot_total,
    player_aliases,
):
    match_date = find_date_field(participants[0][1])
    formatted_line, labels_by_player, result_fields_by_player = format_match_line(
        participants,
        player_aliases,
    )

    if not has_winners_and_losers(labels_by_player):
        return False

    print(formatted_line)
    output_files.formatted_lines.append(formatted_line)
    output_files.match_history_blocks.append(
        match_history_block(
            match_id,
            match_date,
            participants,
            labels_by_player,
            player_aliases,
            bot_total,
        )
    )

    for player, entry in dedupe_participants(participants):
        result = labels_by_player.get(player, "unknown")
        add_player_stats(player_stats, player, entry, result, player_aliases)
        output_files.raw_export_rows.append({
            "match_id": match_id,
            "date": match_date,
            "player": player,
            "output_name": display_name_for_player(player, player_aliases),
            "leader": leader_label(entry),
            "map": map_label(entry),
            "formatted_result": result,
            "result_fields": result_fields_by_player.get(player, {}),
            "raw_entry": entry,
        })

    return True


def process_qualifying_matches(
    api_client,
    qualifying,
    include_bot_games,
    tracked_players,
    player_aliases,
):
    output_files = OutputFiles()
    player_stats = {}
    run_stats = MatchRunStats()
    match_details_cache = load_match_details_cache()

    for match_id, history_participants in sorted(
        qualifying.items(),
        key=match_date_sort_key,
    ):
        verified_match = verified_participants_for_match(
            api_client,
            match_id,
            history_participants,
            include_bot_games,
            tracked_players,
            match_details_cache,
            run_stats,
        )
        if verified_match is None:
            continue

        participants, bot_total = verified_match
        was_added = add_match_to_outputs(
            output_files,
            player_stats,
            match_id,
            participants,
            bot_total,
            player_aliases,
        )
        if not was_added:
            run_stats.skipped_same_side += 1
            continue

        if bot_total:
            run_stats.included_bot_matches += 1

    return output_files, player_stats, run_stats


def print_run_summary(output_files, run_stats, include_bot_games):
    print("=" * 70)
    print(f"\n{len(output_files.formatted_lines)} custom head-to-head matches printed.")
    print(f"{run_stats.fast_path_matches} matches used fast history-row results.")
    print(f"{run_stats.detail_checked_matches} matches checked with full details.")
    print(
        f"{run_stats.verified_suspicious_matches} suspicious matches verified "
        "with full details."
    )
    print(
        f"{run_stats.skipped_missing_details} matches skipped because details "
        "were unavailable."
    )
    if include_bot_games:
        print(f"{run_stats.included_bot_matches} bot matches included.")
        print(f"{run_stats.skipped_unlisted_players} matches with untracked humans skipped.")
        print(
            f"{run_stats.skipped_bot_team_without_human} bot matches skipped because "
            "a team had no human player."
        )
    else:
        print(
            f"{run_stats.skipped_unlisted_players} matches with untracked players "
            "or bots skipped."
        )
    print(f"{run_stats.skipped_same_side} same-side matches skipped.")
    print(f"\nFormatted matches saved to {FORMATTED_OUTPUT_FILE}")
    print(f"Readable match history saved to {MATCH_HISTORY_OUTPUT_FILE}")
    print(f"Stats summary saved to {STATS_OUTPUT_FILE}")
    print(f"Full details saved to {RAW_EXPORT_FILE}")


def run(include_bot_games):
    ensure_api_key_is_configured()

    tracked_players = load_tracked_players()
    player_aliases = load_player_aliases()
    start_date = parse_date(START_DATE, "START_DATE")
    end_date = parse_date(END_DATE, "END_DATE")
    api_client = HaloApiClient(BASE_URL, HEADERS)
    include_bot_games = include_bot_games or INCLUDE_BOT_GAMES

    matches_by_id = collect_match_history(
        api_client,
        tracked_players,
        start_date,
        end_date,
    )
    qualifying = qualifying_matches(matches_by_id)

    print_search_summary(matches_by_id, qualifying, start_date, end_date, include_bot_games)
    output_files, player_stats, run_stats = process_qualifying_matches(
        api_client,
        qualifying,
        include_bot_games,
        tracked_players,
        player_aliases,
    )
    write_output_files(output_files, player_stats)
    print_run_summary(output_files, run_stats, include_bot_games)
