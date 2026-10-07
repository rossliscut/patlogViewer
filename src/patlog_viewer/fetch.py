"""Download only the .pat files inside a time window, or read a local directory."""

from __future__ import annotations

import json
import re
import socket
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path

from download_robot_logs.client import SYNC, VERSION, RobodClient, pack, recv_exact
from download_robot_logs.download import (
    extra_pats,
    jobs_from_paths,
    list_5130,
    robot_now,
)
from download_robot_logs.select import PAT_DIRS, filter_rotated, parse_last, parse_stamp

ROBOT_PORT = 19208
_NAME_PORT = 19204
_NAME_API = 1100
_ROBOT_ID = re.compile(r"^\d{4}$")
_NESTED_ID = re.compile(r"^10\.5 (\d{4})$")
_NESTED_LABEL = re.compile(r"^10\.5 (\d{4} .+)$")
_ALIASES = {"taskchain": "7004"}


def name_from_status(data: dict) -> str:
    for key in ("vehicle_id", "robot_note"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("机器人没有返回名称")


def robot_name(host: str, timeout: float = 5.0) -> str:
    """Read the robot name from status API 1100 on port 19204."""
    host = host.strip()
    if not host:
        raise ValueError("需要 IP")
    try:
        sock = socket.create_connection((host, _NAME_PORT), timeout)
    except OSError as exc:
        raise ValueError(f"连不上 {host}") from exc
    try:
        sock.settimeout(timeout)
        sock.sendall(pack(_NAME_API, b"{}"))
        header = recv_exact(sock, 16)
        sync, ver, _seq, length, res_api = struct.unpack(">BBHIH", header[:10])
        if sync != SYNC or ver != VERSION or res_api != _NAME_API + 10000:
            raise ValueError(f"{host} 返回的名称数据无效")
        body = recv_exact(sock, length) if length else b""
    except (OSError, ConnectionError) as exc:
        raise ValueError(f"连不上 {host}") from exc
    finally:
        sock.close()
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{host} 返回的名称数据无效") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{host} 返回的名称数据无效")
    return name_from_status(data)


def refresh_robot_names(robots: list[dict], lookup) -> tuple[list[dict], bool]:
    """Retry the name for robots saved with only an IP. Known names stay as they are."""
    updated = []
    changed = False
    for robot in robots:
        host = str(robot.get("host") or "").strip()
        current = str(robot.get("id") or "").strip()
        port = int(robot.get("port") or ROBOT_PORT)
        if current and current != host:
            updated.append({"id": current, "host": host, "port": port})
            continue
        try:
            name = lookup(host)
        except ValueError:
            name = host
        if name != current:
            changed = True
        updated.append({"id": name, "host": host, "port": ROBOT_PORT})
    return updated, changed


def parse_window(text: str) -> timedelta:
    return parse_last(text)


def newest_pat(files: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The .pat whose filename time is latest. Names without a time sort last."""
    stamped: list[tuple[datetime, str, str]] = []
    plain: list[tuple[str, str]] = []
    for name, full in files:
        if not str(name).endswith(".pat"):
            continue
        stamp = parse_stamp(name)
        if stamp is None:
            plain.append((name, full))
        else:
            stamped.append((stamp, name, full))
    if stamped:
        _stamp, name, full = max(stamped)
        return name, full
    if plain:
        name, full = max(plain)
        return name, full
    return None


def _pats_in(client: RobodClient, directory: str) -> list[tuple[str, str]]:
    payload = json.dumps({"path": directory}, separators=(",", ":")).encode("utf-8")
    try:
        api, blob = client.call(5100, payload, 30)
    except ConnectionRefusedError as exc:
        raise ConnectionError("端口 19208 拒绝了连接") from exc
    except OSError as exc:
        raise ConnectionError(f"连不上：{exc}") from exc
    if api != 15100 or not blob:
        return []
    try:
        data = json.loads(blob.decode("utf-8"))
    except json.JSONDecodeError:
        return []
    found: list[tuple[str, str]] = []
    for item in data.get("file_list") or []:
        if item.get("is_dir"):
            continue
        name = str(item.get("name") or "")
        if not name.endswith(".pat"):
            continue
        raw = str(item.get("file_path") or "").replace("\\", "/")
        full = raw if raw.startswith("/") else directory.rstrip("/") + "/" + name
        found.append((name, full))
    return found


def latest_pat_on_robot(client: RobodClient) -> tuple[str, str] | None:
    errors: list[ConnectionError] = []
    listed = False
    for directory in PAT_DIRS:
        try:
            found = _pats_in(client, directory)
        except ConnectionError as exc:
            errors.append(exc)
            continue
        listed = True
        if found:
            return newest_pat(found)
    if not listed and errors:
        raise errors[0]
    return None


def read_with_progress(sock: socket.socket, length: int, on_progress=None) -> bytes:
    """Read a framed body. 5101 puts the file size in the header before the bytes."""
    buf = bytearray()
    next_report = 0.0
    while len(buf) < length:
        chunk = sock.recv(min(1024 * 1024, length - len(buf)))
        if not chunk:
            raise ConnectionError(f"closed after {len(buf)}/{length}")
        buf += chunk
        if on_progress is None:
            continue
        now = time.monotonic()
        if len(buf) == length or now >= next_report:
            on_progress(len(buf), length)
            next_report = now + 0.4
    return bytes(buf)


def _call_with_progress(client: RobodClient, api: int, payload: bytes, timeout: float, on_progress):
    last: Exception | None = None
    for _ in range(3):
        try:
            if client.sock is None:
                client.connect()
            assert client.sock is not None
            client.sock.settimeout(timeout)
            client.sock.sendall(pack(api, payload))
            header = recv_exact(client.sock, 16)
            sync, ver, _seq, length, res_api = struct.unpack(">BBHIH", header[:10])
            if sync != SYNC or ver != VERSION:
                raise ConnectionError("bad header")
            body = read_with_progress(client.sock, length, on_progress) if length else b""
            return res_api, body
        except Exception as exc:  # noqa: BLE001
            last = exc
            client.close()
    assert last is not None
    raise last


def download_with_progress(client: RobodClient, full: str, dest: Path, on_progress=None) -> None:
    parent, name = full.rsplit("/", 1)
    payload = json.dumps({"path": parent, "file_name": name}, separators=(",", ":")).encode("utf-8")
    _api, blob = _call_with_progress(client, 5101, payload, 300.0, on_progress)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)


def select_pat_paths(
    paths: list[str],
    window_start: datetime,
    window_end: datetime,
    download_time: datetime,
) -> list[tuple[str, str]]:
    """Keep patlog files whose filename span overlaps the window. Nothing else."""
    jobs = jobs_from_paths(paths, False)
    pats = [(rel, full) for rel, full in jobs if "/patlogs/" in rel and rel.endswith(".pat")]
    allowed = set(
        filter_rotated([rel for rel, _full in pats], window_start, window_end, download_time)
    )
    return [(rel, full) for rel, full in pats if rel in allowed]


def fetch_pats(
    host: str,
    port: int,
    dest: Path,
    *,
    last: timedelta | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    latest: bool = False,
    on_progress=None,
) -> list[Path]:
    """List and download .pat files. Does not build a debug zip."""
    client = RobodClient(host, port)
    try:
        if latest:
            try:
                chosen_one = latest_pat_on_robot(client)
            except ConnectionError as exc:
                raise RuntimeError(f"连不上 {host}：{exc}") from exc
            if chosen_one is None:
                return []
            _name, full = chosen_one
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / Path(full).name
            download_with_progress(client, full, target, on_progress)
            return [target]
        if last is not None:
            download_time = robot_now(client)
            window_end = download_time
            window_start = download_time - last
        else:
            if start is None or end is None:
                raise RuntimeError("pass a window")
            window_start = start
            window_end = end
            try:
                download_time = robot_now(client)
            except Exception:
                download_time = window_end
        if window_start >= window_end:
            raise RuntimeError(f"empty window {window_start} -> {window_end}")
        paths = list_5130(client, window_start, window_end, False)
        paths.extend(extra_pats(client, paths))
        chosen = select_pat_paths(paths, window_start, window_end, download_time)
        saved: list[Path] = []
        dest.mkdir(parents=True, exist_ok=True)
        for rel, full in chosen:
            target = dest / Path(rel).name
            download_with_progress(client, full, target, on_progress)
            saved.append(target)
        return saved
    finally:
        client.close()


def robot_id_for(folder: Path) -> str:
    name = folder.name
    if name in _ALIASES:
        return _ALIASES[name]
    if _ROBOT_ID.fullmatch(name):
        return name
    nested = _NESTED_ID.fullmatch(name)
    if nested:
        return nested.group(1)
    labeled = _NESTED_LABEL.fullmatch(name)
    if labeled:
        return labeled.group(1)
    return name


def discover_local(root: Path) -> list[dict]:
    """Each directory that directly holds .pat files is one robot."""
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if depth > 3 or folder.name == "__pycache__":
            return
        pats = [path for path in folder.glob("*.pat") if path.is_file()]
        if pats:
            found.append(folder)
            return
        if depth == 3:
            return
        for child in sorted(folder.iterdir(), key=lambda path: path.name):
            if child.is_dir():
                walk(child, depth + 1)

    walk(root, 0)
    used: dict[str, int] = {}
    robots: list[dict] = []
    for folder in found:
        robot_id = robot_id_for(folder)
        used[robot_id] = used.get(robot_id, 0) + 1
        if used[robot_id] > 1:
            robot_id = f"{robot_id} {folder.name}"
        robots.append({"id": robot_id, "path": str(folder)})
    return robots


def pat_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.glob("*.pat") if path.is_file())
