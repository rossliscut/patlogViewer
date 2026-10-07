"""PAT frames and one Mid-360 acceleration sample per ImuArray packet."""

from __future__ import annotations

import math
import struct
from datetime import datetime, timedelta
from pathlib import Path

EPOCH = datetime(1970, 1, 1)
IMU_NAME = b"mvt.protocol.ImuArray"


def to_seconds(moment: datetime) -> float:
    """Seconds since 1970-01-01 with no timezone conversion."""
    return (moment - EPOCH).total_seconds()


def from_seconds(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def read_varint(data: bytes, index: int) -> tuple[int, int]:
    number = 0
    shift = 0
    while index < len(data):
        byte = data[index]
        index += 1
        number |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return number, index
        shift += 7
    raise EOFError(index)


def iter_frames(data: bytes):
    """Yield (name, payload). A broken frame ends the scan."""
    index = 0
    size = len(data)
    while index + 16 <= size:
        body = struct.unpack_from("<I", data, index)[0]
        end = index + 4 + body
        if body < 16 or end > size:
            return
        name_len = struct.unpack_from("<I", data, index + 12)[0]
        if name_len < 2 or index + 16 + name_len > end:
            return
        name = data[index + 16 : index + 16 + name_len].split(b"\x00", 1)[0]
        payload = data[index + 16 + name_len : end]
        yield name, payload
        index = end


def sensor_timestamp(payload: bytes, imu_tag: int) -> int | None:
    start = imu_tag - 1
    stop = max(0, imu_tag - 12) - 1
    while start > stop:
        try:
            value, end = read_varint(payload, start)
        except EOFError:
            start -= 1
            continue
        if end == imu_tag and start > 0 and payload[start - 1] == 0x08:
            return value
        start -= 1
    return None


def parse_imu(payload: bytes) -> tuple[int, tuple[float, float, float]] | None:
    found = payload.find(b"imu")
    if found < 0 or found > 60 or payload[found - 2 : found] != b"\x22\x03":
        return None
    sensor_ts = sensor_timestamp(payload, found - 2)
    if sensor_ts is None:
        return None
    cursor = found + 3
    try:
        _key, cursor = read_varint(payload, cursor)
        _host, cursor = read_varint(payload, cursor)
    except EOFError:
        return None
    window = payload[cursor : cursor + 48]
    relative = window.find(b"\x1a\x18")
    if relative < 0 or cursor + relative + 26 > len(payload):
        return None
    ax, ay, az = struct.unpack_from("<3d", payload, cursor + relative + 2)
    if not all(math.isfinite(value) for value in (ax, ay, az)):
        return None
    if any(abs(value) > 80 for value in (ax, ay, az)):
        return None
    magnitude = math.sqrt(ax * ax + ay * ay + az * az)
    if not 0.5 < magnitude < 80:
        return None
    return sensor_ts, (ax, ay, az)


def filename_time(name: str) -> datetime:
    stem = Path(name).name
    if stem.lower().endswith(".pat"):
        stem = stem[: -len(".pat")]
    if stem.startswith("pat_") or stem.startswith("pat-"):
        stamp = stem[4:]
    else:
        raise ValueError(name)
    return datetime.strptime(stamp, "%Y-%m-%d-%H-%M-%S")


def load_file(path: Path) -> list[tuple[datetime, float, float, float]]:
    """Wall clock is the filename time plus the sensor delta inside this file."""
    opened = filename_time(path.name)
    rows: list[tuple[datetime, float, float, float]] = []
    origin: int | None = None
    for name, payload in iter_frames(path.read_bytes()):
        if name != IMU_NAME:
            continue
        parsed = parse_imu(payload)
        if parsed is None:
            continue
        sensor_ts, (ax, ay, az) = parsed
        if origin is None:
            origin = sensor_ts
        wall = opened + timedelta(seconds=(sensor_ts - origin) / 1e9)
        rows.append((wall, ax, ay, az))
    return rows


def load_paths(paths: list[Path]) -> tuple[list[tuple[datetime, float, float, float]], list[str]]:
    rows: list[tuple[datetime, float, float, float]] = []
    notes: list[str] = []
    for path in paths:
        try:
            rows.extend(load_file(path))
        except ValueError as exc:
            notes.append(f"{path.name}: {exc}")
    rows.sort(key=lambda row: row[0])
    return rows, notes
