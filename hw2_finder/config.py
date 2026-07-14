import argparse
import os


ENV_FILE = ".env"
TRACKED_PLAYERS_FILE = "tracked_players.txt"
PLAYER_ALIASES_FILE = "player_aliases.json"


def load_env_file(path=ENV_FILE):
    """Load KEY=value pairs from a local .env file without extra packages."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find Halo Wars 2 custom matches between tracked players.",
    )
    parser.add_argument(
        "--include-bot-games",
        action="store_true",
        help=(
            "Slower: fetch full details for matches with extra players, then "
            "include bot games only when every human is tracked and each team "
            "has at least one human."
        ),
    )
    return parser.parse_args()


load_env_file()

# Environment-backed settings
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
INCLUDE_BOT_GAMES = env_flag("INCLUDE_BOT_GAMES")

OUTPUT_DIR = "output"
FORMATTED_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "formatted_matches.txt")
RAW_EXPORT_FILE = os.path.join(OUTPUT_DIR, "group_matches_export.json")
STATS_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "stats_summary.txt")
MATCH_HISTORY_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "match_history.txt")
MATCH_DETAILS_CACHE_FILE = os.path.join(OUTPUT_DIR, "match_details_cache.json")
