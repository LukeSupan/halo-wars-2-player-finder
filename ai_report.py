"""
Build a local analysis report from Halo Wars 2 custom match exports.

Default usage:
    python ai_report.py

To refresh match data first:
    python ai_report.py --refresh

To append Codex CLI narrative commentary:
    python ai_report.py --ai codex
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


EXPORT_FILE = "group_matches_export.json"
DEFAULT_REPORT_FILE = "analysis_report.md"


def empty_record() -> dict[str, int]:
    return {"wins": 0, "losses": 0, "games": 0}


def add_result(record: dict[str, int], result: str) -> None:
    if result not in ("win", "loss"):
        return
    record["games"] += 1
    if result == "win":
        record["wins"] += 1
    else:
        record["losses"] += 1


def winrate(record: dict[str, int]) -> float:
    if record["games"] == 0:
        return 0.0
    return record["wins"] / record["games"]


def bayes_score(record: dict[str, int], prior_games: int = 4) -> float:
    """Shrink tiny samples toward 50% so one lucky game does not become S tier."""
    return (record["wins"] + (prior_games * 0.5)) / (record["games"] + prior_games)


def tier_for(record: dict[str, int], min_games: int) -> str:
    if record["games"] < min_games:
        return "Provisional"

    score = bayes_score(record)
    if score >= 0.72:
        return "S"
    if score >= 0.60:
        return "A"
    if score >= 0.50:
        return "B"
    if score >= 0.40:
        return "C"
    return "D"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def record_text(record: dict[str, int]) -> str:
    return f"{record['wins']}-{record['losses']}"


def parse_date(value) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("ISO8601Date")
    if not value:
        return None

    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_duration_seconds(value) -> float | None:
    if not value:
        return None

    # Halo uses ISO-8601 duration strings such as PT21M17.2540442S.
    import re

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


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(round(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Run `python main.py` first, or use `python ai_report.py --refresh`."
        )

    with path.open("r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise SystemExit(f"{path} must contain a JSON array.")
    return rows


def player_name(row: dict) -> str:
    return str(row.get("output_name") or row.get("player") or "unknown player")


def row_team(row: dict):
    raw_entry = row.get("raw_entry") or {}
    return raw_entry.get("TeamId")


def row_duration(row: dict) -> float | None:
    raw_entry = row.get("raw_entry") or {}
    return parse_duration_seconds(raw_entry.get("PlayerMatchDuration"))


def group_matches(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        match_id = row.get("match_id")
        if match_id:
            grouped[match_id].append(row)

    matches = []
    for match_id, match_rows in grouped.items():
        dates = [parse_date(row.get("date")) for row in match_rows]
        dates = [date for date in dates if date is not None]
        durations = [row_duration(row) for row in match_rows]
        durations = [duration for duration in durations if duration is not None]
        match_rows.sort(
            key=lambda row: (
                str(row_team(row)) if row_team(row) is not None else "",
                player_name(row).lower(),
            )
        )
        matches.append(
            {
                "id": match_id,
                "rows": match_rows,
                "date": min(dates) if dates else None,
                "map": str(match_rows[0].get("map") or "unknown map"),
                "duration": max(durations) if durations else None,
            }
        )

    matches.sort(key=lambda match: match["date"] or datetime.min.replace(tzinfo=timezone.utc))
    return matches


def collect_stats(matches: list[dict]) -> dict:
    players = defaultdict(empty_record)
    leaders = defaultdict(empty_record)
    maps = defaultdict(empty_record)
    player_leaders = defaultdict(empty_record)
    player_maps = defaultdict(empty_record)
    leader_players = defaultdict(lambda: defaultdict(empty_record))
    map_players = defaultdict(lambda: defaultdict(empty_record))
    head_to_head = defaultdict(lambda: {"first_wins": 0, "second_wins": 0, "games": 0})
    leader_matchups = defaultdict(lambda: {"first_wins": 0, "second_wins": 0, "games": 0})
    teammate_pairs = defaultdict(empty_record)
    match_shapes = Counter()

    for match in matches:
        rows = [row for row in match["rows"] if row.get("formatted_result") in ("win", "loss")]
        teams = defaultdict(list)
        for row in rows:
            result = row["formatted_result"]
            name = player_name(row)
            leader = str(row.get("leader") or "unknown leader")
            map_name = str(row.get("map") or match["map"] or "unknown map")
            team = row_team(row)

            add_result(players[name], result)
            add_result(leaders[leader], result)
            add_result(maps[map_name], result)
            add_result(player_leaders[(name, leader)], result)
            add_result(player_maps[(name, map_name)], result)
            add_result(leader_players[leader][name], result)
            add_result(map_players[map_name][name], result)
            teams[team if team is not None else result].append(row)

        win_count = sum(1 for row in rows if row.get("formatted_result") == "win")
        loss_count = sum(1 for row in rows if row.get("formatted_result") == "loss")
        if win_count and loss_count:
            match_shapes[f"{win_count}v{loss_count}"] += 1

        for first, second in itertools.combinations(rows, 2):
            first_result = first.get("formatted_result")
            second_result = second.get("formatted_result")
            if first_result == second_result:
                continue

            names = sorted([player_name(first), player_name(second)], key=str.lower)
            key = tuple(names)
            first_name = player_name(first)
            first_key_player = key[0]
            key_record = head_to_head[key]
            key_record["games"] += 1
            if first_name == first_key_player:
                key_record["first_wins"] += int(first_result == "win")
                key_record["second_wins"] += int(second_result == "win")
            else:
                key_record["first_wins"] += int(second_result == "win")
                key_record["second_wins"] += int(first_result == "win")

            leaders_pair = sorted(
                [str(first.get("leader") or "unknown leader"), str(second.get("leader") or "unknown leader")],
                key=str.lower,
            )
            leader_key = tuple(leaders_pair)
            first_leader = str(first.get("leader") or "unknown leader")
            first_key_leader = leader_key[0]
            leader_record = leader_matchups[leader_key]
            leader_record["games"] += 1
            if first_leader == first_key_leader:
                leader_record["first_wins"] += int(first_result == "win")
                leader_record["second_wins"] += int(second_result == "win")
            else:
                leader_record["first_wins"] += int(second_result == "win")
                leader_record["second_wins"] += int(first_result == "win")

        for team_rows in teams.values():
            if len(team_rows) < 2:
                continue
            for first, second in itertools.combinations(team_rows, 2):
                names = tuple(sorted([player_name(first), player_name(second)], key=str.lower))
                add_result(teammate_pairs[names], first.get("formatted_result", "unknown"))

    return {
        "players": dict(players),
        "leaders": dict(leaders),
        "maps": dict(maps),
        "player_leaders": dict(player_leaders),
        "player_maps": dict(player_maps),
        "leader_players": {leader: dict(records) for leader, records in leader_players.items()},
        "map_players": {map_name: dict(records) for map_name, records in map_players.items()},
        "head_to_head": dict(head_to_head),
        "leader_matchups": dict(leader_matchups),
        "teammate_pairs": dict(teammate_pairs),
        "match_shapes": match_shapes,
    }


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def top_records(records: dict, limit: int = 5, min_games: int = 1):
    return sorted(
        (
            (name, record)
            for name, record in records.items()
            if record["games"] >= min_games
        ),
        key=lambda item: (-bayes_score(item[1]), -item[1]["games"], item[0].lower()),
    )[:limit]


def player_tier_rows(stats: dict, min_games: int) -> list[list[str]]:
    rows = []
    for name, record in sorted(
        stats["players"].items(),
        key=lambda item: (-bayes_score(item[1]), -item[1]["games"], item[0].lower()),
    ):
        best_leaders = [
            (leader, leader_record)
            for (player, leader), leader_record in stats["player_leaders"].items()
            if player == name and leader_record["games"] > 0
        ]
        best_leaders.sort(key=lambda item: (-bayes_score(item[1]), -item[1]["games"], item[0]))
        leader_note = ", ".join(
            f"{leader} {record_text(leader_record)}"
            for leader, leader_record in best_leaders[:2]
        ) or "none"
        rows.append(
            [
                tier_for(record, min_games),
                name,
                record_text(record),
                pct(winrate(record)),
                str(record["games"]),
                f"{bayes_score(record):.3f}",
                leader_note,
            ]
        )
    return rows


def leader_tier_rows(stats: dict, min_games: int) -> list[list[str]]:
    rows = []
    for leader, record in sorted(
        stats["leaders"].items(),
        key=lambda item: (-bayes_score(item[1]), -item[1]["games"], item[0].lower()),
    ):
        pilots = top_records(stats["leader_players"].get(leader, {}), limit=2)
        pilot_note = ", ".join(
            f"{player} {record_text(player_record)}"
            for player, player_record in pilots
        ) or "none"
        rows.append(
            [
                tier_for(record, min_games),
                leader,
                record_text(record),
                pct(winrate(record)),
                str(record["games"]),
                f"{bayes_score(record):.3f}",
                pilot_note,
            ]
        )
    return rows


def matchup_lines(stats: dict, min_games: int = 2) -> list[str]:
    lines = []
    head_to_head = sorted(
        stats["head_to_head"].items(),
        key=lambda item: (-item[1]["games"], item[0][0].lower(), item[0][1].lower()),
    )
    for (first, second), record in head_to_head[:8]:
        if record["games"] < min_games:
            continue
        if record["first_wins"] == record["second_wins"]:
            result_note = "series is even"
        else:
            leader = first if record["first_wins"] > record["second_wins"] else second
            result_note = f"{leader} leads"
        lines.append(
            f"- {first} vs {second}: {first} {record['first_wins']}-{record['second_wins']} "
            f"over {record['games']} games ({result_note})."
        )

    teammate_pairs = sorted(
        stats["teammate_pairs"].items(),
        key=lambda item: (-item[1]["games"], -bayes_score(item[1]), item[0][0].lower()),
    )
    synergy_lines = []
    for (first, second), record in teammate_pairs[:5]:
        if record["games"] < min_games:
            continue
        synergy_lines.append(
            f"- {first} + {second}: {record_text(record)} ({pct(winrate(record))}) as teammates."
        )

    leader_matchups = sorted(
        stats["leader_matchups"].items(),
        key=lambda item: (-item[1]["games"], item[0][0].lower(), item[0][1].lower()),
    )
    leader_lines = []
    for (first, second), record in leader_matchups[:5]:
        if record["games"] < min_games:
            continue
        leader_lines.append(
            f"- {first} vs {second}: {first} {record['first_wins']}-{record['second_wins']} "
            f"over {record['games']} leader clashes."
        )

    if not lines:
        lines.append("- Not enough repeated opponent matchups yet for reliable head-to-head notes.")
    if synergy_lines:
        lines.extend(["", "Best repeated teammate samples:"])
        lines.extend(synergy_lines)
    if leader_lines:
        lines.extend(["", "Repeated leader clashes:"])
        lines.extend(leader_lines)
    return lines


def map_trend_rows(stats: dict, matches: list[dict]) -> list[list[str]]:
    match_counts = Counter(match["map"] for match in matches)
    durations = defaultdict(list)
    for match in matches:
        if match["duration"] is not None:
            durations[match["map"]].append(match["duration"])

    rows = []
    for map_name, record in sorted(
        stats["maps"].items(),
        key=lambda item: (-match_counts[item[0]], -item[1]["games"], item[0].lower()),
    ):
        top_players = top_records(stats["map_players"].get(map_name, {}), limit=2)
        top_note = ", ".join(
            f"{player} {record_text(player_record)}"
            for player, player_record in top_players
        ) or "none"
        rows.append(
            [
                map_name,
                str(match_counts[map_name]),
                str(record["games"]),
                duration_text(sum(durations[map_name]) / len(durations[map_name])) if durations[map_name] else "unknown",
                top_note,
            ]
        )
    return rows


def practice_suggestions(stats: dict) -> list[str]:
    suggestions = []
    for player, record in sorted(stats["players"].items(), key=lambda item: item[0].lower()):
        weak_leaders = [
            (leader, leader_record)
            for (name, leader), leader_record in stats["player_leaders"].items()
            if name == player and leader_record["games"] >= 2 and winrate(leader_record) < 0.45
        ]
        weak_leaders.sort(key=lambda item: (winrate(item[1]), -item[1]["games"], item[0]))

        weak_maps = [
            (map_name, map_record)
            for (name, map_name), map_record in stats["player_maps"].items()
            if name == player and map_record["games"] >= 2 and winrate(map_record) < 0.45
        ]
        weak_maps.sort(key=lambda item: (winrate(item[1]), -item[1]["games"], item[0]))

        losing_matchups = []
        for (first, second), matchup_record in stats["head_to_head"].items():
            if player not in (first, second) or matchup_record["games"] < 2:
                continue
            player_wins = matchup_record["first_wins"] if player == first else matchup_record["second_wins"]
            opponent_wins = matchup_record["second_wins"] if player == first else matchup_record["first_wins"]
            if player_wins < opponent_wins:
                opponent = second if player == first else first
                losing_matchups.append((opponent, player_wins, opponent_wins, matchup_record["games"]))
        losing_matchups.sort(key=lambda item: (item[1] - item[2], -item[3], item[0]))

        notes = []
        if weak_leaders:
            leader, leader_record = weak_leaders[0]
            notes.append(f"review {leader} games ({record_text(leader_record)})")
        if weak_maps:
            map_name, map_record = weak_maps[0]
            notes.append(f"drill openings on {map_name} ({record_text(map_record)})")
        if losing_matchups:
            opponent, player_wins, opponent_wins, games = losing_matchups[0]
            notes.append(f"prep specifically for {opponent} ({player_wins}-{opponent_wins} over {games})")
        if not notes:
            if record["games"] < 4:
                notes.append("collect more games before changing strategy")
            else:
                notes.append("rotate into weaker or lower-sample leaders to avoid one-dimensional practice")

        suggestions.append(f"- {player}: " + "; ".join(notes) + ".")

    return suggestions


def report_facts(stats: dict, matches: list[dict]) -> dict:
    dates = [match["date"] for match in matches if match["date"] is not None]
    durations = [match["duration"] for match in matches if match["duration"] is not None]
    return {
        "matches": len(matches),
        "player_appearances": sum(record["games"] for record in stats["players"].values()),
        "date_start": min(dates).date().isoformat() if dates else "unknown",
        "date_end": max(dates).date().isoformat() if dates else "unknown",
        "average_duration": duration_text(sum(durations) / len(durations)) if durations else "unknown",
        "players": len(stats["players"]),
        "leaders": len(stats["leaders"]),
        "maps": len(stats["maps"]),
        "match_shapes": ", ".join(
            f"{shape}: {count}" for shape, count in stats["match_shapes"].most_common()
        )
        or "unknown",
    }


def build_report(rows: list[dict], min_games: int) -> str:
    matches = group_matches(rows)
    stats = collect_stats(matches)
    facts = report_facts(stats, matches)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Halo Wars 2 AI Analysis Report",
        "",
        f"Generated: {generated_at}",
        "",
        "This report is generated from `group_matches_export.json`. It does not change `formatted_matches.txt`.",
        "",
        "## Run Summary",
        "",
        f"- Matches analyzed: {facts['matches']}",
        f"- Player appearances: {facts['player_appearances']}",
        f"- Date range: {facts['date_start']} to {facts['date_end']}",
        f"- Average match duration: {facts['average_duration']}",
        f"- Players / leaders / maps: {facts['players']} / {facts['leaders']} / {facts['maps']}",
        f"- Match shapes: {facts['match_shapes']}",
        "",
        "## Player Tier List",
        "",
        "Tiers use a small-sample-adjusted winrate, so tiny records are marked provisional.",
        "",
    ]
    lines.extend(
        table(
            ["Tier", "Player", "Record", "Winrate", "Games", "Score", "Best leader samples"],
            player_tier_rows(stats, min_games),
        )
    )
    lines.extend(
        [
            "",
            "## Leader Tier List",
            "",
            "Leader tiers aggregate all tracked player appearances.",
            "",
        ]
    )
    lines.extend(
        table(
            ["Tier", "Leader", "Record", "Winrate", "Games", "Score", "Best pilots"],
            leader_tier_rows(stats, min_games),
        )
    )
    lines.extend(["", "## Matchup Observations", ""])
    lines.extend(matchup_lines(stats))
    lines.extend(["", "## Map Trends", ""])
    lines.extend(
        table(
            ["Map", "Matches", "Player apps", "Avg duration", "Top samples"],
            map_trend_rows(stats, matches),
        )
    )
    lines.extend(["", "## Practice Suggestions", ""])
    lines.extend(practice_suggestions(stats))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- The report is descriptive, not a perfect skill rating. Team games count each player appearance.",
            "- For resume framing, this is a local agent-assisted analytics pipeline: API ingestion, structured export, deterministic analysis, and optional Codex CLI narrative generation.",
            "",
        ]
    )

    return "\n".join(lines)


def build_codex_prompt(report: str) -> str:
    return (
        "You are analyzing a Halo Wars 2 custom-game report. "
        "Use only the facts in the markdown below. Do not invent missing stats. "
        "Return a concise markdown section titled `## Codex AI Commentary` with: "
        "1) the biggest strategic pattern, 2) one surprising or fragile data point, "
        "3) three practical practice priorities. Keep it friendly and specific.\n\n"
        + report
    )


def run_codex_commentary(report: str, command: str, model: str | None) -> str:
    executable = shutil.which(command) or command
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "codex_commentary.md"
        cmd = [
            executable,
            "exec",
            "--cd",
            str(Path.cwd()),
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(output_path),
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")

        try:
            subprocess.run(
                cmd,
                input=build_codex_prompt(report),
                text=True,
                capture_output=True,
                check=True,
                timeout=180,
            )
        except FileNotFoundError as exc:
            raise SystemExit(f"Could not find Codex command `{command}`: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "no stderr"
            raise SystemExit(f"Codex commentary failed: {stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemExit("Codex commentary timed out after 180 seconds.") from exc

        if output_path.exists():
            return output_path.read_text(encoding="utf-8").strip()
    return ""


def refresh_outputs() -> None:
    subprocess.run([sys.executable, "main.py"], check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Halo Wars 2 markdown analysis report.")
    parser.add_argument("--input", default=EXPORT_FILE, help=f"JSON export to read. Default: {EXPORT_FILE}")
    parser.add_argument("--output", default=DEFAULT_REPORT_FILE, help=f"Markdown report path. Default: {DEFAULT_REPORT_FILE}")
    parser.add_argument("--refresh", action="store_true", help="Run main.py before building the report.")
    parser.add_argument("--min-games", type=int, default=4, help="Minimum games before non-provisional tiers. Default: 4")
    parser.add_argument("--ai", choices=["none", "codex"], default="none", help="Append optional AI commentary. Default: none")
    parser.add_argument(
        "--codex-command",
        default="codex.cmd" if os.name == "nt" else "codex",
        help="Codex CLI executable for --ai codex. Default: codex.cmd on Windows, codex elsewhere.",
    )
    parser.add_argument("--model", default=None, help="Optional Codex model override for --ai codex.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.refresh:
        refresh_outputs()

    rows = load_rows(Path(args.input))
    report = build_report(rows, args.min_games)

    if args.ai == "codex":
        commentary = run_codex_commentary(report, args.codex_command, args.model)
        if commentary:
            report = report.rstrip() + "\n\n" + commentary.strip() + "\n"

    output_path = Path(args.output)
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
