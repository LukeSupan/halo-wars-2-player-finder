import json
import os
import sys

from .config import (
    FORMATTED_OUTPUT_FILE,
    MATCH_HISTORY_OUTPUT_FILE,
    PLAYER_ALIASES_FILE,
    RAW_EXPORT_FILE,
    STATS_OUTPUT_FILE,
    TRACKED_PLAYERS_FILE,
)
from .formatting import build_stats_summary


def load_tracked_players(path=TRACKED_PLAYERS_FILE):
    if not os.path.exists(path):
        print(f"Missing {path}. Add one gamertag per line, then re-run.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as player_file:
        players = [
            raw_line.strip()
            for raw_line in player_file
            if raw_line.strip() and not raw_line.strip().startswith("#")
        ]

    if not players:
        print(f"No players found in {path}. Add one gamertag per line, then re-run.")
        sys.exit(1)

    return players


def load_player_aliases(path=PLAYER_ALIASES_FILE):
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as aliases_file:
        try:
            aliases = json.load(aliases_file)
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


def write_output_files(output_files, player_stats):
    os.makedirs(os.path.dirname(FORMATTED_OUTPUT_FILE), exist_ok=True)

    with open(FORMATTED_OUTPUT_FILE, "w", encoding="utf-8") as formatted_file:
        formatted_file.write("\n".join(output_files.formatted_lines))
        if output_files.formatted_lines:
            formatted_file.write("\n")

    with open(RAW_EXPORT_FILE, "w", encoding="utf-8") as export_file:
        json.dump(output_files.raw_export_rows, export_file, indent=2, default=str)

    with open(STATS_OUTPUT_FILE, "w", encoding="utf-8") as stats_file:
        stats_file.write(build_stats_summary(player_stats))

    with open(MATCH_HISTORY_OUTPUT_FILE, "w", encoding="utf-8") as history_file:
        history_file.write("\n\n".join(output_files.match_history_blocks))
        if output_files.match_history_blocks:
            history_file.write("\n")
