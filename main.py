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


load_env_file()

API_KEY = os.environ.get("HALO_API_KEY", "")
START_DATE = os.environ.get("START_DATE", "").strip()
BASE_URL = "https://www.haloapi.com"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}

MATCH_TYPES = ["custom"]
PAGE_SIZE = 25
REQUEST_DELAY = 0.6
MAX_RETRIES = 3
MIN_TRACKED_PLAYERS = 2  # only include matches with at least this many
CUSTOM_MATCH_TYPE_ID = 2
MIN_MATCH_DURATION_SECONDS = 180
FORMATTED_OUTPUT_FILE = "formatted_matches.txt"
RAW_EXPORT_FILE = "group_matches_export.json"
STATS_OUTPUT_FILE = "stats_summary.txt"

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


def fetch_player_history(player, match_type, start_date=None):
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
            if not is_before_start_date(entry, start_date)
        ]
        entries.extend(visible_results)

        if start_date and results and not visible_results:
            break
        if len(results) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY)
    return entries


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
    for k, v in _flatten(entry).items():
        if "time" in k.lower() or "date" in k.lower():
            return v
    return None


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


def is_before_start_date(entry, start_date):
    if not start_date:
        return False

    match_date = parse_date(find_date_field(entry), "match date")
    return bool(match_date and match_date < start_date)


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


def is_long_enough_match(participants):
    durations = [
        parse_duration_seconds(entry.get("PlayerMatchDuration"))
        for _, entry in dedupe_participants(participants)
    ]
    durations = [duration for duration in durations if duration is not None]
    return not durations or max(durations) >= MIN_MATCH_DURATION_SECONDS


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
    match_map = defaultdict(list)  # match_id -> [(player, entry), ...]

    for player in tracked_players:
        for match_type in MATCH_TYPES:
            print(f"Fetching {match_type} history for {player}...")
            entries = fetch_player_history(player, match_type, start_date)
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
    print(
        f"{len(qualifying)} custom matches lasted at least 3 minutes "
        f"and had {MIN_TRACKED_PLAYERS}+ tracked players.\n"
    )
    print("Formatted matches:")
    print("=" * 70)

    export_rows = []
    formatted_lines = []
    stats = {}
    skipped_unlisted_players = 0
    skipped_same_side = 0
    for mid, participants in sorted(
        qualifying.items(),
        key=lambda kv: str(find_date_field(kv[1][0][1]) or "")
    ):
        if not has_only_tracked_players(participants):
            skipped_unlisted_players += 1
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

        for player, entry in dedupe_participants(participants):
            result = labels_by_player.get(player, "unknown")
            add_player_stats(stats, player, entry, result, player_aliases)
            export_rows.append({
                "match_id": mid,
                "date": date,
                "player": player,
                "output_name": display_name_for_player(player, player_aliases),
                "leader": leader_label(entry),
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

    print("=" * 70)
    print(f"\n{len(formatted_lines)} custom head-to-head matches printed.")
    print(f"{skipped_unlisted_players} matches with unlisted players skipped.")
    print(f"{skipped_same_side} same-side matches skipped.")
    print(f"\nFormatted matches saved to {FORMATTED_OUTPUT_FILE}")
    print(f"Stats summary saved to {STATS_OUTPUT_FILE}")
    print(f"Full details saved to {RAW_EXPORT_FILE}")


if __name__ == "__main__":
    main()
