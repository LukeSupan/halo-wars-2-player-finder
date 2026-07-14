"""
Halo Wars 2 - group match finder
Uses the OFFICIAL Halo Public API (https://developer.haloapi.com)

Finds matches where at least two players from a tracked list played
together, then prints and saves copy-friendly win/loss output.

Run with:
    python main.py
"""

from hw2_finder.config import parse_args
from hw2_finder.runner import run


def main():
    args = parse_args()
    run(include_bot_games=args.include_bot_games)


if __name__ == "__main__":
    main()
