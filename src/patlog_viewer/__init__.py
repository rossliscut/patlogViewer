"""Fetch robot patlogs and show Mid-360 acceleration."""

from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"
STARTED = datetime.now()


def built_at(root: Path | None = None, started: datetime | None = None) -> str:
    """Later of this process start and the newest program file."""
    folder = root or Path(__file__).resolve().parent
    newest = (started or STARTED).timestamp()
    for path in folder.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        newest = max(newest, path.stat().st_mtime)
    return datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S")
