import json
import os
import sys
import time
from urllib.parse import quote

from .config import MAX_RETRIES, MATCH_DETAILS_CACHE_FILE, PAGE_SIZE, REQUEST_DELAY
from .match_rules import is_after_end_date, is_before_start_date


def get_requests():
    try:
        import requests
    except ModuleNotFoundError:
        print("Missing dependency: requests")
        print("Install it with: pip install -r requirements.txt")
        sys.exit(1)
    return requests


class HaloApiClient:
    """Small wrapper around the Halo Public API calls used by this script."""

    def __init__(self, base_url, headers):
        self.base_url = base_url
        self.headers = headers
        self.requests = get_requests()

    def get_json(self, url, params=None):
        """Make a GET request, handle Halo API rate limits, and return JSON."""
        response = None
        for retry_number in range(1, MAX_RETRIES + 1):
            response = self.requests.get(url, headers=self.headers, params=params)

            if response.status_code == 429:
                wait_seconds = int(response.headers.get("Retry-After", 5))
                print(
                    f"    rate limited on attempt {retry_number}, "
                    f"waiting {wait_seconds}s..."
                )
                time.sleep(wait_seconds)
                continue

            if response.status_code == 404:
                return None  # no history of this type / gamertag not found

            response.raise_for_status()
            return response.json()

        if response is not None:
            response.raise_for_status()
        return None

    def fetch_player_history(self, player, match_type, start_date=None, end_date=None):
        """Return raw match-history rows for one player and one match type."""
        entries = []
        first_result_index = 0
        encoded_player = quote(player, safe="")

        while True:
            # API call: GET /stats/hw2/players/{gamertag}/matches
            url = f"{self.base_url}/stats/hw2/players/{encoded_player}/matches"
            query_params = {
                "start": first_result_index,
                "count": PAGE_SIZE,
                "matchType": match_type,
            }
            data = self.get_json(url, params=query_params)
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

            first_result_index += PAGE_SIZE
            time.sleep(REQUEST_DELAY)

        return entries


def load_match_details_cache(path=MATCH_DETAILS_CACHE_FILE):
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as cache_file:
        try:
            cache = json.load(cache_file)
        except json.JSONDecodeError:
            print(f"Ignoring invalid {path}; it will be rebuilt.")
            return {}

    if not isinstance(cache, dict):
        print(f"Ignoring invalid {path}; it will be rebuilt.")
        return {}
    return cache


def save_match_details_cache(cache, path=MATCH_DETAILS_CACHE_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, indent=2, default=str)


def fetch_match_details(api_client, match_id, cache=None):
    if cache is not None and match_id in cache:
        return cache[match_id], True

    # API call: GET /stats/hw2/matches/{match_id}
    url = f"{api_client.base_url}/stats/hw2/matches/{match_id}"
    match_details = api_client.get_json(url)
    if cache is not None and match_details:
        cache[match_id] = match_details
    return match_details, False
