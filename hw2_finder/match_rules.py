import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

from .config import CUSTOM_MATCH_TYPE_ID, MIN_MATCH_DURATION_SECONDS
from .halo_metadata import LEADER_NAMES, MAP_NAMES


def find_result_fields(entry):
    """Recursively find any key that looks like it encodes win/loss/rank."""
    hits = {}
    keywords = ("outcome", "result", "winner", "rank")

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = f"{path}.{key}" if path else key
                if (
                    any(keyword in key.lower() for keyword in keywords)
                    and not isinstance(value, (dict, list))
                ):
                    hits[new_path] = value
                walk(value, new_path)
        elif isinstance(node, list):
            for list_index, value in enumerate(node[:3]):  # cap fan-out
                walk(value, f"{path}[{list_index}]")

    walk(entry)
    return hits


def _flatten(entry, path=""):
    flattened_entry = {}
    if isinstance(entry, dict):
        for key, value in entry.items():
            new_path = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                flattened_entry.update(_flatten(value, new_path))
            else:
                flattened_entry[new_path] = value
    return flattened_entry


def find_date_field(entry):
    flattened = _flatten(entry)

    preferred_names = (
        "matchstartdate.iso8601date",
        "matchstartdate",
        "startdate",
        "matchdate",
        "date",
        "timestamp",
    )
    for preferred_name in preferred_names:
        for key, value in flattened.items():
            if key.lower().endswith(preferred_name) and is_date_like_value(value):
                return value

    for key, value in flattened.items():
        normalized_key = key.lower()
        if (
            (
                "date" in normalized_key
                or "timestamp" in normalized_key
                or ("start" in normalized_key and "time" in normalized_key)
            )
            and "duration" not in normalized_key
            and "timeinmatch" not in normalized_key
            and is_date_like_value(value)
        ):
            return value
    return None


def is_date_like_value(value):
    if not value:
        return False

    raw_value = str(value).strip()
    if raw_value.upper().startswith("P"):
        return False

    return bool(re.match(r"\d{4}-\d{2}-\d{2}", raw_value))


def parse_date(value, setting_name="date"):
    if not value:
        return None

    raw_value = str(value).strip()
    normalized = raw_value.replace("Z", "+00:00")
    try:
        if len(raw_value) == 10:
            return datetime.fromisoformat(raw_value).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        print(f"Invalid {setting_name}: {raw_value}")
        print("Use YYYY-MM-DD, like START_DATE=2026-07-14")
        sys.exit(1)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def readable_date(value):
    parsed = parse_date(value, "match date")
    if not parsed:
        return "unknown date"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def is_before_start_date(entry, start_date):
    if not start_date:
        return False

    match_date = parse_date(find_date_field(entry), "match date")
    return bool(match_date and match_date < start_date)


def is_after_end_date(entry, end_date):
    if not end_date:
        return False

    match_date = parse_date(find_date_field(entry), "match date")
    return bool(match_date and match_date > end_date)


def guess_label(value):
    if value == 1:
        return "win"
    if value == 2:
        return "loss"
    if value == 3:
        return "tie"

    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in ("1", "win", "won", "victory", "victorious", "w"):
            return "win"
        if normalized_value in ("2", "loss", "lost", "defeat", "defeated", "l"):
            return "loss"
        if normalized_value in ("3", "tie", "draw"):
            return "tie"
    return "unknown"


def result_for_entry(entry):
    team_id = entry.get("TeamId")
    teams = entry.get("Teams")
    if team_id is not None and isinstance(teams, dict):
        team = teams.get(str(team_id)) or teams.get(team_id)
        if isinstance(team, dict) and "MatchOutcome" in team:
            return guess_label(team["MatchOutcome"]), {
                "Teams.TeamId.MatchOutcome": team["MatchOutcome"]
            }

    if "PlayerMatchOutcome" in entry:
        return guess_label(entry["PlayerMatchOutcome"]), {
            "PlayerMatchOutcome": entry["PlayerMatchOutcome"]
        }

    result_fields = find_result_fields(entry)
    for key, value in result_fields.items():
        label = guess_label(value)
        if label != "unknown":
            return label, result_fields
    return "unknown", result_fields


def dedupe_participants(participants):
    seen = set()
    deduped = []
    for player, entry in participants:
        key = player.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((player, entry))
    return deduped


def normalize_gamertag(gamertag):
    return gamertag.strip().lower()


def tracked_player_lookup(tracked_players):
    return {
        normalize_gamertag(player): player
        for player in tracked_players
    }


def match_players(match_details):
    players = match_details.get("Players", {})
    if not isinstance(players, dict):
        return []
    return list(players.values())


def human_gamertags(match_details):
    gamertags = []
    for player in match_players(match_details):
        if not player.get("IsHuman"):
            continue
        human_id = player.get("HumanPlayerId") or {}
        gamertag = human_id.get("Gamertag")
        if not gamertag:
            return None
        gamertags.append(gamertag)
    return gamertags


def bot_count(match_details):
    return sum(1 for player in match_players(match_details) if not player.get("IsHuman"))


def team_key(team_id):
    if team_id is None:
        return None
    return str(team_id)


def has_human_on_each_team(match_details):
    teams_with_players = set()
    teams_with_humans = set()

    for player in match_players(match_details):
        team_id = team_key(player.get("TeamId"))
        if team_id is None:
            return False

        teams_with_players.add(team_id)
        if player.get("IsHuman"):
            teams_with_humans.add(team_id)

    return len(teams_with_players) >= 2 and teams_with_players <= teams_with_humans


def has_only_tracked_humans(match_details, tracked_players):
    gamertags = human_gamertags(match_details)
    if gamertags is None:
        return False

    tracked = set(tracked_player_lookup(tracked_players))
    return all(normalize_gamertag(gamertag) in tracked for gamertag in gamertags)


def tracked_participants_from_details(match_details, tracked_players):
    lookup = tracked_player_lookup(tracked_players)
    participants = []

    for player in match_players(match_details):
        if not player.get("IsHuman"):
            continue

        human_id = player.get("HumanPlayerId") or {}
        gamertag = human_id.get("Gamertag")
        if not gamertag:
            continue

        tracked_name = lookup.get(normalize_gamertag(gamertag))
        if not tracked_name:
            continue

        entry = dict(player)
        entry["MatchId"] = match_details.get("MatchId")
        entry["MatchType"] = match_details.get("MatchType")
        entry["GameMode"] = match_details.get("GameMode")
        entry["MapId"] = match_details.get("MapId")
        entry["MatchStartDate"] = match_details.get("MatchStartDate")
        entry["PlayerMatchDuration"] = (
            match_details.get("MatchDuration") or player.get("TimeInMatch")
        )
        entry["Teams"] = match_details.get("Teams")
        participants.append((tracked_name, entry))

    return participants


def is_custom_match(participants):
    return any(
        entry.get("MatchType") == CUSTOM_MATCH_TYPE_ID
        for _, entry in dedupe_participants(participants)
    )


def total_players_from_entry(entry):
    teams = entry.get("Teams")
    if not isinstance(teams, dict) or not teams:
        return None

    total = 0
    for team in teams.values():
        if not isinstance(team, dict) or "TeamSize" not in team:
            return None
        try:
            total += int(team["TeamSize"])
        except (TypeError, ValueError):
            return None
    return total


def total_players_in_match(participants):
    for _, entry in dedupe_participants(participants):
        total = total_players_from_entry(entry)
        if total is not None:
            return total
    return None


def has_only_tracked_players(participants):
    total_players = total_players_in_match(participants)
    if total_players is None:
        return False
    return len(dedupe_participants(participants)) == total_players


def player_completed_match(entry):
    value = entry.get("PlayerCompletedMatch")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return True


def has_incomplete_player(participants):
    return any(
        not player_completed_match(entry)
        for _, entry in dedupe_participants(participants)
    )


def has_mixed_results_on_same_team(participants):
    labels_by_team = defaultdict(set)
    for _, entry in dedupe_participants(participants):
        team_id = entry.get("TeamId")
        if team_id is None:
            continue

        label, _ = result_for_entry(entry)
        if label in ("win", "loss"):
            labels_by_team[team_id].add(label)

    return any(
        "win" in labels and "loss" in labels
        for labels in labels_by_team.values()
    )


def needs_match_detail_verification(participants):
    return (
        has_incomplete_player(participants)
        or has_mixed_results_on_same_team(participants)
    )


def parse_duration_seconds(value):
    if not value:
        return None

    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        str(value).strip(),
    )
    if not match:
        return None

    days = float(match.group("days") or 0)
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def readable_duration(value):
    seconds = parse_duration_seconds(value)
    if seconds is None:
        return "unknown duration"

    return format_duration_seconds(seconds)


def format_duration_seconds(seconds):
    total_seconds = int(round(seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"


def is_long_enough_match(participants):
    durations = [
        parse_duration_seconds(entry.get("PlayerMatchDuration"))
        for _, entry in dedupe_participants(participants)
    ]
    durations = [duration for duration in durations if duration is not None]
    return not durations or max(durations) >= MIN_MATCH_DURATION_SECONDS


def map_label(entry):
    map_id = entry.get("MapId")
    if not map_id:
        return "unknown map"
    return MAP_NAMES.get(map_id, map_id)


def readable_match_duration(participants):
    durations = [
        parse_duration_seconds(entry.get("PlayerMatchDuration"))
        for _, entry in dedupe_participants(participants)
    ]
    durations = [duration for duration in durations if duration is not None]
    if not durations:
        return "unknown duration"
    return format_duration_seconds(max(durations))


def leader_label(entry):
    leader_id = entry.get("LeaderId")
    if leader_id is None:
        return "unknown leader"
    return LEADER_NAMES.get(leader_id, f"leader {leader_id}")
