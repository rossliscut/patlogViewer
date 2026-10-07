import json
import socket
import threading
from datetime import datetime
from pathlib import Path

from download_robot_logs.client import pack

from patlog_viewer.fetch import (
    discover_local,
    name_from_status,
    latest_pat_on_robot,
    newest_pat,
    read_with_progress,
    refresh_robot_names,
    robot_id_for,
    robot_name,
    select_pat_paths,
)


def _pat(name: str) -> str:
    return "/usr/local/etc/.SeerRobotics/rbk/diagnosis/log/patlogs/" + name


def test_select_pat_paths_drops_other_files_and_old_segments() -> None:
    paths = [
        _pat("pat-2026-10-02-10-00-00.pat"),
        _pat("pat-2026-10-02-13-19-31.pat"),
        "/usr/local/etc/.SeerRobotics/rbk/diagnosis/log/robokit_2026-10-02-13-19-31.log",
        "/tmp/maps/site.smap",
    ]
    chosen = select_pat_paths(
        paths,
        datetime(2026, 10, 2, 13, 19, 0),
        datetime(2026, 10, 2, 13, 50, 0),
        datetime(2026, 10, 2, 13, 50, 0),
    )
    assert [path.name for _rel, path in [(item[0], Path(item[1])) for item in chosen]] == [
        "pat-2026-10-02-13-19-31.pat"
    ]


def test_robot_id_aliases() -> None:
    assert robot_id_for(Path("taskchain")) == "7004"
    assert robot_id_for(Path("7005")) == "7005"
    assert robot_id_for(Path("10.5 7001")) == "7001"
    assert robot_id_for(Path("10.5 7004 new Mid-360")) == "7004 new Mid-360"


def test_discover_local(tmp_path: Path) -> None:
    (tmp_path / "taskchain").mkdir()
    (tmp_path / "taskchain" / "pat-2026-10-02-13-19-31.pat").write_bytes(b"")
    (tmp_path / "7005").mkdir()
    (tmp_path / "7005" / "pat_2026-10-02-13-56-22.pat").write_bytes(b"")
    found = {item["id"]: Path(item["path"]).name for item in discover_local(tmp_path)}
    assert found == {"7004": "taskchain", "7005": "7005"}


def test_refresh_robot_names_retries_only_unknown() -> None:
    def lookup(host: str) -> str:
        if host == "10.0.0.2":
            raise ValueError("连不上")
        return "RIL-AP-7004"

    robots, changed = refresh_robot_names(
        [
            {"id": "10.0.0.1", "host": "10.0.0.1", "port": 19208},
            {"id": "10.0.0.2", "host": "10.0.0.2", "port": 19208},
            {"id": "Already", "host": "10.0.0.3", "port": 19208},
        ],
        lookup,
    )
    assert changed is True
    assert robots[0]["id"] == "RIL-AP-7004"
    assert robots[1]["id"] == "10.0.0.2"
    assert robots[2]["id"] == "Already"


def test_latest_pat_reports_a_refused_connection() -> None:
    class Refused:
        def call(self, api: int, payload: bytes, timeout: float):
            raise ConnectionRefusedError("refused")

    try:
        latest_pat_on_robot(Refused())
    except ConnectionError as exc:
        assert "拒绝" in str(exc)
    else:
        raise AssertionError("expected a connection error")


def test_read_with_progress_reports_the_header_length() -> None:
    left, right = socket.socketpair()
    right.sendall(b"abcdefghij")
    right.close()
    seen: list[tuple[int, int]] = []
    body = read_with_progress(left, 10, lambda got, total: seen.append((got, total)))
    left.close()
    assert body == b"abcdefghij"
    assert seen[-1] == (10, 10)


def test_newest_pat_uses_the_filename_time() -> None:
    chosen = newest_pat(
        [
            ("pat-2026-10-02-10-00-00.pat", "/a/pat-2026-10-02-10-00-00.pat"),
            ("pat-2026-10-02-13-19-31.pat", "/a/pat-2026-10-02-13-19-31.pat"),
            ("notes.txt", "/a/notes.txt"),
        ]
    )
    assert chosen == ("pat-2026-10-02-13-19-31.pat", "/a/pat-2026-10-02-13-19-31.pat")


def test_name_from_status_prefers_vehicle_id() -> None:
    assert name_from_status({"vehicle_id": " RIL-AP-7004 ", "robot_note": "note"}) == "RIL-AP-7004"
    assert name_from_status({"vehicle_id": "  ", "robot_note": "note"}) == "note"


def test_robot_name_reads_status_api() -> None:
    payload = json.dumps({"vehicle_id": "RIL-AP-7004"}).encode()
    ready = threading.Event()

    def serve() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        ready.port = port
        ready.set()
        conn, _addr = server.accept()
        with conn:
            conn.recv(64)
            conn.sendall(pack(11100, payload))
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    assert ready.wait(2)
    import patlog_viewer.fetch as fetch

    original = fetch._NAME_PORT
    fetch._NAME_PORT = ready.port
    try:
        assert robot_name("127.0.0.1", timeout=2) == "RIL-AP-7004"
    finally:
        fetch._NAME_PORT = original
