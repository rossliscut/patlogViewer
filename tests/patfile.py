"""Tiny PAT files with one ImuArray sample per packet."""

from __future__ import annotations

import struct
from pathlib import Path


def _varint(number: int) -> bytes:
    out = bytearray()
    while True:
        byte = number & 0x7F
        number >>= 7
        if number:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def imu_payload(sensor_ts: int, ax: float, ay: float, az: float) -> bytes:
    return b"\x08" + _varint(sensor_ts) + b"\x22\x03imu\x00\x00\x1a\x18" + struct.pack("<3d", ax, ay, az)


def frame(payload: bytes) -> bytes:
    name = b"mvt.protocol.ImuArray\x00"
    body = 12 + len(name) + len(payload)
    return struct.pack("<IIII", body, body, 0, len(name)) + name + payload


def write_pat(path: Path, samples: list[tuple[int, float, float, float]]) -> None:
    path.write_bytes(b"".join(frame(imu_payload(*sample)) for sample in samples))
