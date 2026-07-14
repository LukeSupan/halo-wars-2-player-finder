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

   For exact session windows, you can use UTC ISO timestamps instead:

   ```env
   START_DATE=2026-07-14T01:30:00Z
   END_DATE=2026-07-14T04:10:00Z
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

   You can also pass dates directly without editing `.env`:

   ```powershell
   python main.py --start 2026-07-14T01:30:00Z --end 2026-07-14T04:10:00Z
   ```

## Automatic PC session export

If you play on PC, `watch_halo_session.ps1` can watch for the Halo Wars 2
process, wait until the game closes, then export only that session's matches:

```powershell
.\watch_halo_session.ps1
```

Start the watcher before opening Halo Wars 2 for the cleanest results. It writes
separate session files so your normal exports are left alone:

- `session_formatted_matches.txt`
- `session_match_history.txt`
- `session_stats_summary.txt`
- `session_group_matches_export.json`

By default the watcher looks for `HaloWars2` and `xgameFinal`, which are common
PC process names for this game. If your process name is different, run:

```powershell
.\watch_halo_session.ps1 -ProcessName YourProcessName
```

The watcher pads the exact process window by 30 seconds at the start and 5
minutes at the end, then waits 2 minutes before exporting so the Halo API has
time to publish recent matches. You can tune those values:

```powershell
.\watch_halo_session.ps1 -StartPaddingSeconds 60 -EndPaddingSeconds 600 -ApiDelaySeconds 180
```

To keep watching for multiple sessions in one Windows login, use continuous
mode:

```powershell
.\watch_halo_session.ps1 -Continuous
```

### Start the watcher automatically with Windows

After you have confirmed the watcher detects your Halo Wars 2 process, install
it as a Windows logon task:

```powershell
.\install_startup_watcher.ps1
```

That creates a user-level Scheduled Task named `Halo Wars 2 Session Watcher`.
It starts when you log into Windows, runs the watcher in continuous mode, and
keeps scanning for Halo Wars 2 in the background. It writes watcher status to
`session_watcher.log`.

To start it immediately without rebooting:

```powershell
Start-ScheduledTask -TaskName "Halo Wars 2 Session Watcher"
```

To remove the startup task:

```powershell
.\uninstall_startup_watcher.ps1
```

The script only checks custom games that lasted at least
`MIN_MATCH_DURATION_SECONDS`. Every player in the match must be listed in
`tracked_players.txt`; matches with bots or unlisted players are skipped. It
prints matches in chronological order when the tracked players include both a
winner and a loser, like:

```text
luke,ray/win|jr,evan/loss
```

It also saves that copy-friendly output to `formatted_matches.txt`, a readable
chronological match list with dates, winners, losers, map names, and durations
in `match_history.txt`, plus a simple stats compilation in `stats_summary.txt`
with:

- overall winrate for each tracked player
- winrate for each tracked player on each leader name

For speed, normal matches use the player-history rows that were already
fetched. If a match looks suspicious because a tracked player did not complete
the match or one team has mixed win/loss results, the script fetches full match
details for only that match and uses team-level outcome data to fix leaver
cases. Those full-detail responses are cached in `match_details_cache.json`.

The generated `formatted_matches.txt`, `match_history.txt`, `stats_summary.txt`,
`group_matches_export.json`, session output files, and
`match_details_cache.json` files are ignored by Git so local test runs do not
get committed by accident.
