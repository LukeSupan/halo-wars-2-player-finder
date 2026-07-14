"""
Halo Wars 2 - group match finder
Uses the OFFICIAL Halo Public API (https://developer.haloapi.com)

Finds matches where at least two players from a tracked list played
together (this automatically excludes bot/solo games - see note below),
and prints who won each one.

SETUP
-----
1. pip install requests
2. Copy .env.example to .env and set HALO_API_KEY
3. Edit tracked_players.txt if your list changes
4. Run: python main.py

HOW "AT LEAST 2 TRACKED PLAYERS" IS DETERMINED
------------------------------------------------
Each tracked player's own match history is pulled. If the same MatchId
shows up in 2+ of those histories, that means 2+ of your tracked humans
were in that game together - a bot opponent never has its own match
history, so solo/bot-only games never qualify. No mode filtering needed.

A NOTE ON ACCURACY
-------------------
The exact field name for "did this player win" isn't in the current
public docs (they're a few years stale). This script searches each
match entry for any key containing "outcome", "result", "winner", or
"rank" and prints what it finds, with a best-effort WIN/LOSS/TIE label
when the raw value is a recognizable word. If labels look off or blank
once you run this for real, send me one full entry from
group_matches_export.json and I'll fix the exact mapping.
"""

import time
import json
import os
import sys
from urllib.parse import quote
from collections import defaultdict

ENV_FILE = ".env"
TRACKED_PLAYERS_FILE = "tracked_players.txt"


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


load_env_file()

API_KEY = os.environ.get("HALO_API_KEY", "")
BASE_URL = "https://www.haloapi.com"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}

MATCH_TYPES = ["matchmaking", "custom"]
PAGE_SIZE = 25
REQUEST_DELAY = 0.6
MAX_RETRIES = 3
MIN_TRACKED_PLAYERS = 2  # only include matches with at least this many


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


def fetch_player_history(player, match_type):
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
        entries.extend(results)
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


def guess_label(value):
    if isinstance(value, str):
        v = value.lower()
        if v in ("win", "won", "victory"):
            return "WIN"
        if v in ("loss", "lost", "defeat"):
            return "LOSS"
        if v in ("tie", "draw"):
            return "TIE"
    return str(value)


def main():
    if not API_KEY or API_KEY == "PASTE_YOUR_SUBSCRIPTION_KEY_HERE":
        print("Set your API key first: copy .env.example to .env, set")
        print("HALO_API_KEY, then re-run.")
        sys.exit(1)

    tracked_players = load_tracked_players()
    match_map = defaultdict(list)  # match_id -> [(player, entry), ...]

    for player in tracked_players:
        for match_type in MATCH_TYPES:
            print(f"Fetching {match_type} history for {player}...")
            entries = fetch_player_history(player, match_type)
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
        if len(participants) >= MIN_TRACKED_PLAYERS
    }

    print(f"\n{len(match_map)} total unique matches seen across tracked players.")
    print(f"{len(qualifying)} matches had {MIN_TRACKED_PLAYERS}+ tracked players "
          f"(real group games, bot/solo games excluded).\n")
    print("=" * 70)

    export_rows = []
    for mid, participants in sorted(
        qualifying.items(),
        key=lambda kv: str(find_date_field(kv[1][0][1]) or "")
    ):
        date = find_date_field(participants[0][1])
        print(f"Match {mid}" + (f"  ({date})" if date else ""))
        for player, entry in participants:
            result_fields = find_result_fields(entry)
            if result_fields:
                key, value = next(iter(result_fields.items()))
                print(f"  {player:20s} -> {guess_label(value):6s} (raw: {key}={value})")
            else:
                print(f"  {player:20s} -> no result field found "
                      f"(entry keys: {list(entry.keys())})")
            export_rows.append({
                "match_id": mid,
                "date": date,
                "player": player,
                "result_fields": result_fields,
                "raw_entry": entry,
            })
        print("-" * 70)

    with open("group_matches_export.json", "w") as f:
        json.dump(export_rows, f, indent=2, default=str)
    print("\nFull details saved to group_matches_export.json")
    print("If the win/loss labels above look wrong or blank, send me one")
    print("full entry from that file and I'll fix the exact field mapping.")


if __name__ == "__main__":
    main()
