# halo-wars-2-player-finder
finds players using the haloapi for stat tracking purposes
specifically intended to be used with my stat tracker PowerLevel
if you copy paste the output in youll see nice formatted winrates and all that.
this checks exclusively custom games. if you want others you can alter this to achieve that.

## Setup

1. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`, then replace the placeholder value:

   ```env
   HALO_API_KEY=your_halo_api_key_here
   START_DATE=
   END_DATE=
   MIN_MATCH_DURATION_SECONDS=180
   ```

   To ignore older or newer games, set `START_DATE` / `END_DATE` with
   `YYYY-MM-DD`, like:

   ```env
   START_DATE=2026-07-14
   END_DATE=2026-07-31
   ```

   To change the short-game cutoff, set `MIN_MATCH_DURATION_SECONDS`. The
   default is `180`, which means 3 minutes.

3. Edit `tracked_players.txt` to change who gets checked. Add one Xbox
   gamertag per line.

4. Optional: edit `player_aliases.json` to change how gamertags print in the
   formatted output:

   ```json
   {
     "holesec": "luke",
     "tekkitcat": "jr"
   }
   ```

   Any gamertag not listed there prints as itself. Aliases cannot contain
   `,`, `/`, or `|` because those characters are used by the output format.

5. Run the finder:

   ```powershell
   python main.py
   ```

The script only checks custom games that lasted at least
`MIN_MATCH_DURATION_SECONDS`. Every player in the match must be listed in
`tracked_players.txt`; matches with unlisted players are skipped. It prints
matches in chronological order when the tracked players include both a winner
and a loser, like:

```text
luke,ray/win|jr,evan/loss
```

It also saves that copy-friendly output to `formatted_matches.txt`, a readable
chronological match list with dates, winners, losers, map names, and durations
in `match_history.txt`, plus a simple stats compilation in `stats_summary.txt`
with:

- overall winrate for each tracked player
- winrate for each tracked player on each leader name

The generated `formatted_matches.txt`, `match_history.txt`, `stats_summary.txt`, and
`group_matches_export.json` files are ignored by Git so local test runs do not
get committed by accident.
