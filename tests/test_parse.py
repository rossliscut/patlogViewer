from datetime import datetime, timedelta
from pathlib import Path

from patlog_viewer.parse import load_file
from patlog_viewer.signals import axis_span, stuck_fraction, summarize
from tests.patfile import write_pat


def test_three_axes_share_one_clock(tmp_path: Path) -> None:
    path = tmp_path / "pat-2026-10-02-13-19-31.pat"
    samples = [(i * 100_000_000, 0.2, -0.4, 9.8) for i in range(20)]
    samples.append((20 * 100_000_000, 0.0, 0.0, 0.0))
    samples.append((21 * 100_000_000, 90.0, 0.0, 0.0))
    write_pat(path, samples)
    rows = load_file(path)
    assert len(rows) == 20
    assert rows[0][0] == datetime(2026, 10, 2, 13, 19, 31)
    assert rows[1][0] - rows[0][0] == timedelta(seconds=0.1)
    assert rows[0][1:] == (0.2, -0.4, 9.8)
    assert stuck_fraction(rows) == 0.0


def test_pinned_az(tmp_path: Path) -> None:
    path = tmp_path / "pat-2026-10-02-13-19-31.pat"
    write_pat(path, [(i * 100_000_000, 0.0, 0.0, -39.127) for i in range(12)])
    rows = load_file(path)
    assert len(rows) == 12
    assert rows[0][1] == rows[0][2] == 0.0
    assert stuck_fraction(rows) == 100.0
    stats = summarize(rows)
    assert stats["ax"]["median"] == 0.0
    assert stats["n"] == len(rows)


def test_axis_span_ignores_short_burst() -> None:
    burst = datetime(2026, 10, 5, 11, 11, 17)
    dense = datetime(2026, 10, 5, 11, 57, 28)
    rows = [
        (burst, 0.0, 0.0, 9.8),
        (burst + timedelta(seconds=2), 0.0, 0.0, 9.8),
    ]
    rows += [(dense + timedelta(seconds=i * 0.1), 0.0, 0.0, 9.8) for i in range(80)]
    start, end = axis_span(rows)
    assert start == dense
    assert end > dense
