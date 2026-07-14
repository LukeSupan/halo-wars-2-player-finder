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
import argparse
import html
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


load_env_file()

API_KEY = os.environ.get("HALO_API_KEY", "")
START_DATE = os.environ.get("START_DATE", "").strip()
END_DATE = os.environ.get("END_DATE", "").strip()
BASE_URL = "https://www.haloapi.com"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}

MATCH_TYPES = ["custom"]
PAGE_SIZE = 25
REQUEST_DELAY = 0.6
MAX_RETRIES = 3
MIN_TRACKED_PLAYERS = 2  # only include matches with at least this many
CUSTOM_MATCH_TYPE_ID = 2
MIN_MATCH_DURATION_SECONDS = int(os.environ.get("MIN_MATCH_DURATION_SECONDS", "180"))
FORMATTED_OUTPUT_FILE = "formatted_matches.txt"
RAW_EXPORT_FILE = "group_matches_export.json"
STATS_OUTPUT_FILE = "stats_summary.txt"
MATCH_HISTORY_OUTPUT_FILE = "match_history.txt"
MATCH_DETAILS_CACHE_FILE = "match_details_cache.json"
REPORT_OUTPUT_FILE = "report.html"

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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Find Halo Wars 2 custom matches among tracked players.",
    )
    parser.add_argument(
        "--start",
        "--start-date",
        dest="start_date",
        default=START_DATE,
        help=(
            "Only include matches on or after this date/time. Accepts YYYY-MM-DD "
            "or an ISO timestamp like 2026-07-14T01:30:00Z. Defaults to START_DATE."
        ),
    )
    parser.add_argument(
        "--end",
        "--end-date",
        dest="end_date",
        default=END_DATE,
        help=(
            "Only include matches on or before this date/time. Accepts YYYY-MM-DD "
            "or an ISO timestamp like 2026-07-14T04:10:00Z. Defaults to END_DATE."
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="",
        help=(
            "Prefix generated output files, for example 'session' writes "
            "session_formatted_matches.txt."
        ),
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Shortcut for --output-prefix session.",
    )
    return parser.parse_args(argv)


def output_filename(base_name, prefix):
    if not prefix:
        return base_name

    clean_prefix = prefix.strip().strip("_-")
    if not clean_prefix:
        return base_name

    if any(separator in clean_prefix for separator in ("/", "\\", ":")):
        print(f"Invalid output prefix: {prefix}")
        print("Use a simple filename prefix like session or 2026-07-14-session.")
        sys.exit(1)

    return f"{clean_prefix}_{base_name}"


def output_files_for(prefix):
    return {
        "formatted": output_filename(FORMATTED_OUTPUT_FILE, prefix),
        "raw_export": output_filename(RAW_EXPORT_FILE, prefix),
        "stats": output_filename(STATS_OUTPUT_FILE, prefix),
        "match_history": output_filename(MATCH_HISTORY_OUTPUT_FILE, prefix),
        "report": output_filename(REPORT_OUTPUT_FILE, prefix),
    }


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
        print("Use YYYY-MM-DD or an ISO timestamp like 2026-07-14T01:30:00Z.")
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


def html_pre_block(text, empty_message):
    content = text.strip()
    if not content:
        content = empty_message
    return f"<pre>{html.escape(content)}</pre>"


def build_html_report(
    formatted_lines,
    match_history_blocks,
    stats_summary,
    start_date_label,
    end_date_label,
    counts,
):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    formatted_text = "\n".join(formatted_lines)
    match_history_text = "\n\n".join(match_history_blocks)
    window_parts = []
    if start_date_label:
        window_parts.append(f"from {start_date_label}")
    if end_date_label:
        window_parts.append(f"to {end_date_label}")
    window_label = " ".join(window_parts) if window_parts else "all available matches"

    count_cards = [
        ("Printed", counts["printed"]),
        ("Fast path", counts["fast_path"]),
        ("Verified", counts["verified"]),
        ("Skipped untracked", counts["skipped_unlisted"]),
        ("Same side", counts["skipped_same_side"]),
    ]
    cards_html = "\n".join(
        "<div class=\"metric\">"
        f"<span>{html.escape(label)}</span>"
        f"<strong>{value}</strong>"
        "</div>"
        for label, value in count_cards
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Halo Wars 2 Session Report</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101418;
      --panel: #171d23;
      --panel-soft: #1d252c;
      --text: #eef4f8;
      --muted: #9fb0bd;
      --accent: #71d6ff;
      --line: #2b3640;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", system-ui, sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
      margin-bottom: 18px;
    }}
    h1, h2 {{ margin: 0; }}
    h1 {{ font-size: clamp(28px, 4vw, 46px); }}
    h2 {{ font-size: 20px; margin-bottom: 12px; }}
    .subhead {{
      color: var(--muted);
      margin-top: 8px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin: 18px 0;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .metric span {{
      color: var(--muted);
      display: block;
      font-size: 13px;
    }}
    .metric strong {{
      color: var(--accent);
      display: block;
      font-size: 28px;
      margin-top: 4px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 14px;
      padding: 16px;
    }}
    pre {{
      margin: 0;
      padding: 14px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--panel-soft);
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      font: 14px/1.5 Consolas, "Cascadia Mono", monospace;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Halo Wars 2 Session Report</h1>
      <div class="subhead">Window: {html.escape(window_label)} &middot; Generated {generated_at}</div>
    </header>
    <div class="metrics">
      {cards_html}
    </div>
    <section>
      <h2>Formatted Matches</h2>
      {html_pre_block(formatted_text, "No formatted matches found for this session.")}
    </section>
    <section>
      <h2>Stats Summary</h2>
      {html_pre_block(stats_summary, "No stats available for this session.")}
    </section>
    <section>
      <h2>Match History</h2>
      {html_pre_block(match_history_text, "No match history found for this session.")}
    </section>
  </main>
</body>
</html>
"""


def main(argv=None):
    if not API_KEY or API_KEY == "PASTE_YOUR_SUBSCRIPTION_KEY_HERE":
        print("Set your API key first: copy .env.example to .env, set")
        print("HALO_API_KEY, then re-run.")
        sys.exit(1)

    args = parse_args(argv)
    output_prefix = args.output_prefix
    if args.session and not output_prefix:
        output_prefix = "session"
    output_files = output_files_for(output_prefix)

    tracked_players = load_tracked_players()
    player_aliases = load_player_aliases()
    start_date = parse_date(args.start_date, "START_DATE")
    end_date = parse_date(args.end_date, "END_DATE")
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
        print(f"Only checked matches on or after {args.start_date}.")
    if end_date:
        print(f"Only checked matches on or before {args.end_date}.")
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
    fast_path_matches = 0
    verified_suspicious_matches = 0
    skipped_missing_details = 0
    skipped_unlisted_players = 0
    skipped_same_side = 0
    for mid, history_participants in sorted(
        qualifying.items(),
        key=lambda kv: str(find_date_field(kv[1][0][1]) or "")
    ):
        if not has_only_tracked_players(history_participants):
            skipped_unlisted_players += 1
            continue

        participants = history_participants
        match_details = None
        bots = 0
        if needs_match_detail_verification(history_participants):
            match_details, from_cache = fetch_match_details(mid, match_details_cache)
            if not from_cache:
                save_match_details_cache(match_details_cache)
                time.sleep(REQUEST_DELAY)

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

            bots = bot_count(match_details)
            verified_suspicious_matches += 1
        else:
            fast_path_matches += 1

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
                bots,
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

    with open(output_files["formatted"], "w", encoding="utf-8") as f:
        f.write("\n".join(formatted_lines))
        if formatted_lines:
            f.write("\n")

    with open(output_files["raw_export"], "w", encoding="utf-8") as f:
        json.dump(export_rows, f, indent=2, default=str)

    stats_summary = build_stats_summary(stats)
    with open(output_files["stats"], "w", encoding="utf-8") as f:
        f.write(stats_summary)

    with open(output_files["match_history"], "w", encoding="utf-8") as f:
        f.write("\n\n".join(match_history_blocks))
        if match_history_blocks:
            f.write("\n")

    report_html = build_html_report(
        formatted_lines,
        match_history_blocks,
        stats_summary,
        args.start_date,
        args.end_date,
        {
            "printed": len(formatted_lines),
            "fast_path": fast_path_matches,
            "verified": verified_suspicious_matches,
            "skipped_unlisted": skipped_unlisted_players,
            "skipped_same_side": skipped_same_side,
        },
    )
    with open(output_files["report"], "w", encoding="utf-8") as f:
        f.write(report_html)

    print("=" * 70)
    print(f"\n{len(formatted_lines)} custom head-to-head matches printed.")
    print(f"{fast_path_matches} matches used fast history-row results.")
    print(f"{verified_suspicious_matches} suspicious matches verified with full details.")
    print(f"{skipped_missing_details} suspicious matches skipped because details were unavailable.")
    print(f"{skipped_unlisted_players} matches with untracked players or bots skipped.")
    print(f"{skipped_same_side} same-side matches skipped.")
    print(f"\nFormatted matches saved to {output_files['formatted']}")
    print(f"Readable match history saved to {output_files['match_history']}")
    print(f"Stats summary saved to {output_files['stats']}")
    print(f"Full details saved to {output_files['raw_export']}")
    print(f"Session report saved to {output_files['report']}")


if __name__ == "__main__":
    main()
