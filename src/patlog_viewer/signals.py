"""IMU acceleration stats. More signals can register beside imu.acc."""

from __future__ import annotations

from datetime import datetime

STUCK = -39.127
G = 9.8
SIGNAL_ID = "imu.acc"

SIGNALS = [
    {
        "id": SIGNAL_ID,
        "unit": "m/s²",
        "axes": ["ax", "ay", "az"],
        "description": "Mid-360 raw acceleration, one sample per ImuArray packet.",
    }
]


def axis_span(rows: list[tuple[datetime, float, float, float]]) -> tuple[datetime, datetime]:
    """Ends of the time the drawn axis shows.

    Gaps longer than 1 s have no width. A burst shorter than 5 s does not
    set the ends.
    """
    times = [row[0] for row in rows]
    if len(times) < 2:
        return times[0], times[-1]
    segments: list[tuple[datetime, datetime]] = []
    start = 0
    for index in range(1, len(times) + 1):
        if index == len(times) or (times[index] - times[index - 1]).total_seconds() > 1:
            if times[index - 1] > times[start]:
                segments.append((times[start], times[index - 1]))
            start = index
    shown = [segment for segment in segments if (segment[1] - segment[0]).total_seconds() >= 5]
    if not shown:
        shown = segments or [(times[0], times[-1])]
    return shown[0][0], shown[-1][1]


def stuck_fraction(rows: list[tuple[datetime, float, float, float]]) -> float:
    """Time-weighted share of az pinned at -4 g, in percent."""
    covered = 0.0
    stuck = 0.0
    for left, right in zip(rows, rows[1:]):
        dt = (right[0] - left[0]).total_seconds()
        if dt <= 0 or dt > 0.5:
            continue
        covered += dt
        if abs(right[3] - STUCK) < 0.05:
            stuck += dt
    if covered <= 0:
        return 0.0
    return 100.0 * stuck / covered


def median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _axis(values: list[float]) -> dict[str, float]:
    return {"min": min(values), "max": max(values), "median": median(values)}


def format_span(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%Y-%m-%d} {start:%H:%M}–{end:%H:%M}"
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"


def summarize(rows: list[tuple[datetime, float, float, float]]) -> dict:
    start, end = axis_span(rows)
    ax = [row[1] for row in rows]
    ay = [row[2] for row in rows]
    az = [row[3] for row in rows]
    return {
        "n": len(rows),
        "span": format_span(start, end),
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "ax": _axis(ax),
        "ay": _axis(ay),
        "az": _axis(az),
        "pinned": stuck_fraction(rows),
    }


def titles(robot: str, stats: dict) -> dict[str, str]:
    when = stats["span"]
    ax = stats["ax"]["median"]
    ay = stats["ay"]["median"]
    az = stats["az"]["median"]
    share = f"{stats['pinned']:.0f}"
    return {
        "zh": f"{robot}  {when} — ax {ax:+.2f}  ay {ay:+.2f}  az {az:+.2f}，卡死 {share}%",
        "en": f"{robot}  {when} — ax {ax:+.2f}  ay {ay:+.2f}  az {az:+.2f}, stuck {share}%",
    }


def series_document(robot: str, rows: list[tuple[datetime, float, float, float]], sources: list[str]) -> dict:
    from patlog_viewer.parse import to_seconds

    stats = summarize(rows)
    return {
        "id": robot,
        "title": titles(robot, stats),
        "stats": stats,
        "sources": sources,
        "t": [round(to_seconds(row[0]), 3) for row in rows],
        "ax": [round(row[1], 3) for row in rows],
        "ay": [round(row[2], 3) for row in rows],
        "az": [round(row[3], 3) for row in rows],
    }
