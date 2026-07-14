from dataclasses import dataclass, field

from .match_rules import (
    dedupe_participants,
    leader_label,
    map_label,
    readable_date,
    readable_match_duration,
    result_for_entry,
)


@dataclass
class OutputFiles:
    """All files that get written during one run."""

    formatted_lines: list = field(default_factory=list)
    raw_export_rows: list = field(default_factory=list)
    match_history_blocks: list = field(default_factory=list)


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


def readable_result_lines(participants, labels_by_player, player_aliases):
    groups = {"win": [], "loss": [], "tie": [], "unknown": []}
    for player, entry in dedupe_participants(participants):
        label = labels_by_player.get(player, "unknown")
        output_name = display_name_for_player(player, player_aliases)
        groups[label].append(f"{output_name}({leader_label(entry)})")

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
    bot_total=0,
):
    first_entry = dedupe_participants(participants)[0][1]
    lines = [readable_date(date)]
    lines.extend(readable_result_lines(participants, labels_by_player, player_aliases))
    lines.extend([
        f"  Map:      {map_label(first_entry)}",
        f"  Duration: {readable_match_duration(participants)}",
    ])
    if bot_total:
        bot_word = "bot" if bot_total == 1 else "bots"
        lines.append(f"  Bots:     {bot_total} {bot_word}")
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
