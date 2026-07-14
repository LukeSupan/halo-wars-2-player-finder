"""
Halo Wars 2 - group match finder
Uses the OFFICIAL Halo Public API (https://developer.haloapi.com)

Finds matches where at least two players from a tracked list played
together (this automatically excludes bot/solo games - see note below),
and prints who won each one.

SETUP
-----
1. pip install requests
2. Copy .env.example to .env, set HALO_API_KEY, and set START_DATE if you choose
3. Edit tracked_players.txt with your players' usernames
4. In the folder, Run: python main.py

We check for custom games with at least 2 tracked players:
HOW "AT LEAST 2 TRACKED PLAYERS" IS DETERMINED
------------------------------------------------
Each tracked player's own match history is pulled. If the same MatchId
shows up in 2+ of those histories, that means 2+ of your tracked players
were in that game together since a bot opponent never has its own match
history, so solo/bot-only games never qualify. No mode filtering needed.

CUSTOM GAME OUTPUT
------------------
Only custom matches are fetched, the tool could be altered to get real games, but this is for customs. 
Matches are printed only when the trackedplayers include both a winner and a loser, 
which filters out online games where all tracked players were on the same side.

Halo Wars 2 match history uses PlayerMatchOutcome: 1 = win, 2 = loss.
"""

import time
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote
from collections import defaultdict

ENV_FILE = ".env"
TRACKED_PLAYERS_FILE = "tracked_players.txt"
PLAYER_ALIASES_FILE = "player_aliases.json"


def load_env_file(path=ENV_FILE):
    """Load KEY=value pairs from a local .env file without extra packages."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_tracked_players(path=TRACKED_PLAYERS_FILE):
    if not os.path.exists(path):
        print(f"Missing {path}. Add one gamertag per line, then re-run.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        players = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not players:
        print(f"No players found in {path}. Add one gamertag per line, then re-run.")
        sys.exit(1)

    return players


def load_player_aliases(path=PLAYER_ALIASES_FILE):
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        try:
            aliases = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {path}: {exc}")
            sys.exit(1)

    if not isinstance(aliases, dict):
        print(f"{path} must be a JSON object like {{\"holesec\": \"luke\"}}")
        sys.exit(1)

    cleaned = {}
    for gamertag, output_name in aliases.items():
        if not isinstance(gamertag, str) or not isinstance(output_name, str):
            print(f"Every key and value in {path} must be text.")
            sys.exit(1)
        gamertag = gamertag.strip()
        output_name = output_name.strip()
        if gamertag and output_name:
            if any(separator in output_name for separator in (",", "/", "|")):
                print(f"Invalid alias for {gamertag}: {output_name}")
                print("Aliases cannot contain ',', '/', or '|'.")
                sys.exit(1)
            cleaned[gamertag] = output_name

    return cleaned


def env_float(name, default):
    raw_value = os.environ.get(name, str(default)).strip()
    if not raw_value:
        return float(default)

    try:
        return float(raw_value)
    except ValueError:
        print(f"Invalid {name}: {raw_value}")
        print(f"Use a number, like {name}={default}")
        sys.exit(1)


load_env_file()

API_KEY = os.environ.get("HALO_API_KEY", "")
START_DATE = os.environ.get("START_DATE", "").strip()
END_DATE = os.environ.get("END_DATE", "").strip()
BASE_URL = "https://www.haloapi.com"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}

MATCH_TYPES = ["custom"]
PAGE_SIZE = 25
REQUEST_DELAY = 0.6
MATCH_DETAIL_REQUEST_DELAY = env_float("MATCH_DETAIL_REQUEST_DELAY_SECONDS", "1.0")
MAX_RETRIES = 3
MIN_TRACKED_PLAYERS = 2  # only include matches with at least this many
CUSTOM_MATCH_TYPE_ID = 2
MIN_MATCH_DURATION_SECONDS = int(os.environ.get("MIN_MATCH_DURATION_SECONDS", "180"))
FORMATTED_OUTPUT_FILE = "formatted_matches.txt"
RAW_EXPORT_FILE = "group_matches_export.json"
STATS_OUTPUT_FILE = "stats_summary.txt"
MATCH_HISTORY_OUTPUT_FILE = "match_history.txt"
MATCH_DETAILS_CACHE_FILE = "match_details_cache.json"

LEADER_NAMES = {
    1: "Captain Cutter",
    2: "Isabel",
    3: "Professor Anders",
    4: "Decimus",
    5: "Atriox",
    6: "Shipmaster",
    7: "Sergeant Forge",
    8: "Kinsano",
    9: "Commander Jerome",
    10: "The Arbiter",
    11: "Sergeant Johnson",
    12: "Colony",
    13: "Serina",
    14: "Yapyap THE DESTROYER",
    15: "Pavium",
    16: "Voridus",
}

MAP_NAMES = {
    "rostermode\\design\\RM_EvenFlow_Desert\\RM_EvenFlow_Desert": "SIROCCO",
    "rostermode\\design\\RM_EvenFlowArt\\RM_EvenFlowArt": "The Proving Grounds",
    "rostermode\\design\\RM_EvenFlowNight\\RM_EvenFlowNight": "NOCTURNE",
    "skirmish\\design\\Ep02_M03\\Ep02_M03": "FISSURES",
    "skirmish\\design\\FF_StopTheSignal\\FF_StopTheSignal": "HIGH BASTION",
    "skirmish\\design\\fort_jordan\\fort_jordan": "FORT JORDAN",
    "skirmish\\design\\MC_EnforcerValley\\MC_EnforcerValley": "MIRAGE",
    "skirmish\\design\\MP_Boneyard\\MP_Boneyard": "HIGHWAY",
    "skirmish\\design\\MP_Bridges\\MP_Bridges": "FRONTIER",
    "skirmish\\design\\MP_Caldera\\MP_Caldera": "ASHES",
    "skirmish\\design\\MP_Eagle\\MP_Eagle": "BADLANDS",
    "skirmish\\design\\MP_Fracture\\MP_Fracture": "RIFT",
    "skirmish\\design\\MP_Razorblade\\MP_Razorblade": "BEDROCK",
    "skirmish\\design\\MP_Ricochet\\MP_Ricochet": "SENTRY",
    "skirmish\\design\\MP_Veteran\\MP_Veteran": "VAULT",
}


def get_requests():
    try:
        import requests
    except ModuleNotFoundError:
        print("Missing dependency: requests")
        print("Install it with: pip install -r requirements.txt")
        sys.exit(1)
    return requests


def _get(url, params=None):
    requests = get_requests()
    resp = None
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 404:
            return None  # no history of this type / gamertag not found
        resp.raise_for_status()
        return resp.json()
    if resp is not None:
        resp.raise_for_status()
    return None


def fetch_player_history(player, match_type, start_date=None, end_date=None):
    """Returns list of raw match-history entries for one player/type."""
    entries = []
    start = 0
    encoded_player = quote(player, safe="")
    while True:
        url = f"{BASE_URL}/stats/hw2/players/{encoded_player}/matches"
        params = {"start": start, "count": PAGE_SIZE, "matchType": match_type}
        data = _get(url, params=params)
        if not data:
            break
        results = data.get("Results", data) if isinstance(data, dict) else data
        if not results:
            break

        visible_results = [
            entry
            for entry in results
            if (
                not is_before_start_date(entry, start_date)
                and not is_after_end_date(entry, end_date)
            )
        ]
        entries.extend(visible_results)

        if (
            start_date
            and results
            and all(is_before_start_date(entry, start_date) for entry in results)
        ):
            break
        if len(results) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY)
    return entries


def load_match_details_cache(path=MATCH_DETAILS_CACHE_FILE):
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        try:
            cache = json.load(f)
        except json.JSONDecodeError:
            print(f"Ignoring invalid {path}; it will be rebuilt.")
            return {}

    if not isinstance(cache, dict):
        print(f"Ignoring invalid {path}; it will be rebuilt.")
        return {}
    return cache


def save_match_details_cache(cache, path=MATCH_DETAILS_CACHE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, default=str)


def fetch_match_details(match_id, cache=None):
    if cache is not None and match_id in cache:
        return cache[match_id], True

    url = f"{BASE_URL}/stats/hw2/matches/{match_id}"
    match_details = _get(url)
    if cache is not None and match_details:
        cache[match_id] = match_details
    return match_details, False


def find_result_fields(entry):
    """Recursively find any key that looks like it encodes win/loss/rank."""
    hits = {}
    keywords = ("outcome", "result", "winner", "rank")

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                new_path = f"{path}.{k}" if path else k
                if any(kw in k.lower() for kw in keywords) and not isinstance(v, (dict, list)):
                    hits[new_path] = v
                walk(v, new_path)
        elif isinstance(node, list):
            for i, v in enumerate(node[:3]):  # cap fan-out
                walk(v, f"{path}[{i}]")

    walk(entry)
    return hits


def _flatten(entry, path=""):
    out = {}
    if isinstance(entry, dict):
        for k, v in entry.items():
            new_path = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                out.update(_flatten(v, new_path))
            else:
                out[new_path] = v
    return out


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
        v = value.strip().lower()
        if v in ("1", "win", "won", "victory", "victorious", "w"):
            return "win"
        if v in ("2", "loss", "lost", "defeat", "defeated", "l"):
            return "loss"
        if v in ("3", "tie", "draw"):
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


def display_name_for_player(player, player_aliases):
    if player in player_aliases:
        return player_aliases[player]

    lower_aliases = {
        gamertag.lower(): output_name
        for gamertag, output_name in player_aliases.items()
    }
    return lower_aliases.get(player.lower(), player)


def format_match_line(participants, player_aliases=None):
    player_aliases = player_aliases or {}
    groups = {"win": [], "loss": [], "tie": [], "unknown": []}
    labels_by_player = {}
    result_fields_by_player = {}

    for player, entry in dedupe_participants(participants):
        label, result_fields = result_for_entry(entry)
        groups[label].append(display_name_for_player(player, player_aliases))
        labels_by_player[player] = label
        result_fields_by_player[player] = result_fields

    pieces = [
        f"{','.join(groups[label])}/{label}"
        for label in ("win", "loss", "tie", "unknown")
        if groups[label]
    ]
    return "|".join(pieces), labels_by_player, result_fields_by_player


def has_winners_and_losers(labels_by_player):
    labels = set(labels_by_player.values())
    return "win" in labels and "loss" in labels


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


def readable_result_lines(participants, labels_by_player, player_aliases):
    groups = {"win": [], "loss": [], "tie": [], "unknown": []}
    for player, _ in dedupe_participants(participants):
        label = labels_by_player.get(player, "unknown")
        groups[label].append(display_name_for_player(player, player_aliases))

    lines = []
    if groups["win"]:
        lines.append(f"  Winners: {', '.join(groups['win'])}")
    if groups["loss"]:
        lines.append(f"  Losers:  {', '.join(groups['loss'])}")
    if groups["tie"]:
        lines.append(f"  Ties:    {', '.join(groups['tie'])}")
    if groups["unknown"]:
        lines.append(f"  Unknown: {', '.join(groups['unknown'])}")
    return lines


def match_history_block(
    match_id,
    date,
    participants,
    labels_by_player,
    player_aliases,
    bots=0,
):
    first_entry = dedupe_participants(participants)[0][1]
    lines = [readable_date(date)]
    lines.extend(readable_result_lines(participants, labels_by_player, player_aliases))
    lines.extend([
        f"  Map:      {map_label(first_entry)}",
        f"  Duration: {readable_match_duration(participants)}",
    ])
    if bots:
        bot_word = "bot" if bots == 1 else "bots"
        lines.append(f"  Bots:     {bots} {bot_word}")
    lines.append(f"  MatchId:  {match_id}")
    return "\n".join(lines)


def empty_stat_record():
    return {"wins": 0, "losses": 0, "games": 0}


def record_stat(record, result):
    if result not in ("win", "loss"):
        return

    record["games"] += 1
    if result == "win":
        record["wins"] += 1
    else:
        record["losses"] += 1


def leader_label(entry):
    leader_id = entry.get("LeaderId")
    if leader_id is None:
        return "unknown leader"
    return LEADER_NAMES.get(leader_id, f"leader {leader_id}")


def add_player_stats(stats, player, entry, result, player_aliases):
    output_name = display_name_for_player(player, player_aliases)
    player_stats = stats.setdefault(
        output_name,
        {
            "overall": empty_stat_record(),
            "leaders": {},
        },
    )

    record_stat(player_stats["overall"], result)

    leader = leader_label(entry)
    leader_stats = player_stats["leaders"].setdefault(leader, empty_stat_record())
    record_stat(leader_stats, result)


def winrate(record):
    if record["games"] == 0:
        return 0
    return record["wins"] / record["games"] * 100


def stat_line(name, record):
    game_word = "game" if record["games"] == 1 else "games"
    return (
        f"{name}: {record['wins']}-{record['losses']} "
        f"({winrate(record):.1f}%, {record['games']} {game_word})"
    )


def build_stats_summary(stats):
    lines = ["Overall winrates", "=" * 70]
    for player, player_stats in sorted(
        stats.items(),
        key=lambda item: (-item[1]["overall"]["games"], item[0].lower()),
    ):
        lines.append(stat_line(player, player_stats["overall"]))

    lines.extend(["", "Winrates by leader", "=" * 70])
    for player, player_stats in sorted(stats.items(), key=lambda item: item[0].lower()):
        lines.append(player)
        for leader, record in sorted(
            player_stats["leaders"].items(),
            key=lambda item: (-item[1]["games"], item[0]),
        ):
            lines.append(f"  {stat_line(leader, record)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    if not API_KEY or API_KEY == "PASTE_YOUR_SUBSCRIPTION_KEY_HERE":
        print("Set your API key first: copy .env.example to .env, set")
        print("HALO_API_KEY, then re-run.")
        sys.exit(1)

    tracked_players = load_tracked_players()
    player_aliases = load_player_aliases()
    start_date = parse_date(START_DATE, "START_DATE")
    end_date = parse_date(END_DATE, "END_DATE")
    match_map = defaultdict(list)  # match_id -> [(player, entry), ...]

    for player in tracked_players:
        for match_type in MATCH_TYPES:
            print(f"Fetching {match_type} history for {player}...")
            entries = fetch_player_history(player, match_type, start_date, end_date)
            print(f"  {len(entries)} {match_type} matches found")
            for entry in entries:
                match_id = entry.get("MatchId") or entry.get("Id")
                if not match_id:
                    continue
                match_map[match_id].append((player, entry))
            time.sleep(REQUEST_DELAY)

    qualifying = {
        mid: participants
        for mid, participants in match_map.items()
        if (
            len(participants) >= MIN_TRACKED_PLAYERS
            and is_custom_match(participants)
            and is_long_enough_match(participants)
        )
    }

    print(f"\n{len(match_map)} total unique matches seen across tracked players.")
    if start_date:
        print(f"Only checked matches on or after {START_DATE}.")
    if end_date:
        print(f"Only checked matches on or before {END_DATE}.")
    print(
        f"{len(qualifying)} custom matches lasted at least "
        f"{MIN_MATCH_DURATION_SECONDS} seconds "
        f"and had {MIN_TRACKED_PLAYERS}+ tracked players.\n"
    )
    print("Formatted matches:")
    print("=" * 70)

    export_rows = []
    formatted_lines = []
    match_history_blocks = []
    stats = {}
    match_details_cache = load_match_details_cache()
    skipped_missing_details = 0
    skipped_unlisted_players = 0
    skipped_same_side = 0
    for mid, history_participants in sorted(
        qualifying.items(),
        key=lambda kv: str(find_date_field(kv[1][0][1]) or "")
    ):
        match_details, from_cache = fetch_match_details(mid, match_details_cache)
        if not from_cache:
            save_match_details_cache(match_details_cache)
            time.sleep(MATCH_DETAIL_REQUEST_DELAY)

        if not match_details:
            skipped_missing_details += 1
            continue

        if not has_only_tracked_humans(match_details, tracked_players):
            skipped_unlisted_players += 1
            continue

        participants = tracked_participants_from_details(match_details, tracked_players)
        if (
            len(participants) < MIN_TRACKED_PLAYERS
            or not is_custom_match(participants)
            or not is_long_enough_match(participants)
        ):
            continue

        date = find_date_field(participants[0][1])
        formatted_line, labels_by_player, result_fields_by_player = format_match_line(
            participants,
            player_aliases,
        )
        if not has_winners_and_losers(labels_by_player):
            skipped_same_side += 1
            continue

        print(formatted_line)
        formatted_lines.append(formatted_line)
        match_history_blocks.append(
            match_history_block(
                mid,
                date,
                participants,
                labels_by_player,
                player_aliases,
                bot_count(match_details),
            )
        )

        for player, entry in dedupe_participants(participants):
            result = labels_by_player.get(player, "unknown")
            add_player_stats(stats, player, entry, result, player_aliases)
            export_rows.append({
                "match_id": mid,
                "date": date,
                "player": player,
                "output_name": display_name_for_player(player, player_aliases),
                "leader": leader_label(entry),
                "map": map_label(entry),
                "formatted_result": result,
                "result_fields": result_fields_by_player.get(player, {}),
                "raw_entry": entry,
            })

    with open(FORMATTED_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(formatted_lines))
        if formatted_lines:
            f.write("\n")

    with open(RAW_EXPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(export_rows, f, indent=2, default=str)

    with open(STATS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_stats_summary(stats))

    with open(MATCH_HISTORY_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n\n".join(match_history_blocks))
        if match_history_blocks:
            f.write("\n")

    print("=" * 70)
    print(f"\n{len(formatted_lines)} custom head-to-head matches printed.")
    print(f"{skipped_missing_details} matches skipped because details were unavailable.")
    print(f"{skipped_unlisted_players} matches with unlisted human players skipped.")
    print(f"{skipped_same_side} same-side matches skipped.")
    print(f"\nFormatted matches saved to {FORMATTED_OUTPUT_FILE}")
    print(f"Readable match history saved to {MATCH_HISTORY_OUTPUT_FILE}")
    print(f"Stats summary saved to {STATS_OUTPUT_FILE}")
    print(f"Full details saved to {RAW_EXPORT_FILE}")


if __name__ == "__main__":
    main()
