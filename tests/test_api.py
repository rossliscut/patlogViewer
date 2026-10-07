import io
import json
import threading
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from patlog_viewer.app import create_app
from patlog_viewer.report import _summary
from datetime import datetime

from patlog_viewer.schedule import keep_interval, next_fire, resume_fire, tick
from tests.patfile import write_pat


def _client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    return TestClient(app)


def _pat(folder: Path, name: str, az: float, count: int = 12) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_pat(folder / name, [(i * 100_000_000, 0.1, -0.2, az) for i in range(count)])


def test_duplicate_robot_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        same_name = client.put("/api/robots", json=[
            {"id": "RIL-AP-7004", "host": "10.111.87.23"},
            {"id": "RIL-AP-7004", "host": "10.111.87.24"},
        ])
        assert same_name.status_code == 400
        assert same_name.json()["detail"] == "RIL-AP-7004 已经在列表里"
        same_ip = client.put("/api/robots", json=[
            {"id": "RIL-AP-7004", "host": "10.111.87.23"},
            {"id": "RIL-AP-7001", "host": "10.111.87.23"},
        ])
        assert same_ip.status_code == 400
        assert same_ip.json()["detail"] == "10.111.87.23 已经在列表里"
        saved = client.get("/api/robots")
        assert saved.json()["robots"] == []


def test_build_time_follows_a_newer_file(tmp_path: Path) -> None:
    import os
    from datetime import datetime

    from patlog_viewer import built_at

    source = tmp_path / "app.py"
    source.write_text("x", encoding="utf-8")
    moment = datetime(2024, 6, 1, 12, 0, 0)
    os.utime(source, (moment.timestamp(), moment.timestamp()))
    assert built_at(tmp_path, started=datetime(2020, 1, 1)) == "2024-06-01 12:00:00"
    assert built_at(tmp_path, started=datetime(2025, 1, 2, 3, 4, 5)) == "2025-01-02 03:04:05"


def test_version_reports_number_and_build_time(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        body = client.get("/api/version").json()
        assert body["version"] == "0.1.0"
        assert len(body["built_at"]) == 19
        assert body["built_at"][10] == " "


def test_robots_schedule_and_local_run(tmp_path: Path) -> None:
    left = tmp_path / "7004"
    right = tmp_path / "7005"
    _pat(left, "pat-2026-10-02-13-19-31.pat", -39.127)
    _pat(right, "pat-2026-10-02-13-56-22.pat", 9.8)
    with _client(tmp_path) as client:
        saved = client.put("/api/robots", json=[{"id": "7004", "host": "10.0.0.4", "port": 19208}])
        assert saved.status_code == 200
        assert saved.json()["robots"][0]["id"] == "7004"
        bad = client.put("/api/schedule", json={"enabled": False, "interval_minutes": 60, "window": "nope"})
        assert bad.status_code == 400
        schedule = client.put("/api/schedule", json={"enabled": False, "interval_minutes": 30, "window": "20m"})
        assert schedule.json()["interval_minutes"] == 30
        assert schedule.json()["mode"] == "interval"
        assert schedule.json()["next_run"] is None
        assert client.get("/api/signals").json()["signals"][0]["id"] == "imu.acc"
        started = client.post(
            "/api/runs",
            json={"local_dirs": [{"id": "7004", "path": str(left)}, {"id": "7005", "path": str(right)}]},
        )
        assert started.status_code == 200
        run_id = started.json()["id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(run)
        assert run["status"] == "done"
        assert run["source"] == "local"
        assert len(run_id) >= 19 and run_id[4] == "-" and run_id[10] == "_"
        series = client.get(f"/api/runs/{run_id}/series", params={"signal": "imu.acc"}).json()
        by_id = {panel["id"]: panel for panel in series["panels"]}
        assert len(by_id["7004"]["ax"]) == len(by_id["7004"]["ay"]) == len(by_id["7004"]["az"]) == 12
        assert by_id["7004"]["stats"]["pinned"] == 100.0
        assert by_id["7005"]["stats"]["pinned"] == 0.0
        report = client.get(f"/api/runs/{run_id}/report")
        assert report.status_code == 200
        html = report.text
        assert "时间约定" in html and "Time convention" in html
        assert 'id="langbtn"' in html
        assert 'id="downloadbtn"' in html
        assert "下载" in html and "Download" in html
        assert "cdn." not in html
        assert "13936" not in html
        assert "-39.127" in html
        assert (tmp_path / "data" / "reports" / f"{run_id}.html").is_file()
        running = client.get(f"/api/runs/{run_id}/series", params={"signal": "other"})
        assert running.status_code == 404


def test_connect_and_download_are_separate_steps(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    seen: dict = {}

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        run_id = app.state.runner._active
        seen["before"] = app.state.runner.db.get_run(run_id)["robots"][0]["status"]
        on_progress(32, 128)
        seen["during"] = app.state.runner.db.get_run(run_id)["robots"][0]["status"]
        path = dest / "pat-2026-10-02-13-19-31.pat"
        write_pat(path, [(0, 0.0, 0.0, 9.8), (100_000_000, 0.0, 0.0, 9.8)])
        return [path]

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[{"id": "7004", "host": "10.0.0.4"}])
        started = client.post("/api/runs", json={"window": "latest"})
        assert started.status_code == 200
        run_id = started.json()["id"]
        deadline = time.time() + 10
        run = started.json()
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert seen["before"] == "connecting"
        assert seen["during"] == "downloading"
        assert run["status"] == "done"


def test_latest_window_fetches_one_file(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    seen: dict = {}

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        seen["latest"] = latest
        seen["last"] = last
        path = dest / "pat-2026-10-02-13-19-31.pat"
        write_pat(path, [(0, 0.0, 0.0, 9.8), (100_000_000, 0.0, 0.0, 9.8)])
        return [path]

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[{"id": "7004", "host": "10.0.0.4"}])
        saved = client.put("/api/schedule", json={"enabled": False, "interval_minutes": 60, "window": "latest"})
        assert saved.status_code == 200
        assert saved.json()["window"] == "latest"
        started = client.post("/api/runs", json={"window": "latest"})
        assert started.status_code == 200
        run_id = started.json()["id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert seen["latest"] is True
        assert seen["last"] is None


def test_export_includes_the_run_record_and_pat(tmp_path: Path) -> None:
    folder = tmp_path / "7004"
    _pat(folder, "pat-2026-10-02-13-19-31.pat", 9.8, count=2)
    with _client(tmp_path) as client:
        started = client.post(
            "/api/runs",
            json={"local_dirs": [{"id": "7004", "path": str(folder)}]},
        )
        assert started.status_code == 200
        run_id = started.json()["id"]
        deadline = time.time() + 10
        run = started.json()
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        exported = client.get(f"/api/runs/{run_id}/export")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/zip")
        archive = zipfile.ZipFile(io.BytesIO(exported.content))
        names = archive.namelist()
        assert "run.json" in names
        assert any(name.endswith("pat-2026-10-02-13-19-31.pat") for name in names)
        assert any(name.startswith("series/") and name.endswith(".json") for name in names)
        saved = json.loads(archive.read("run.json"))
        assert saved["id"] == run_id
        assert saved["robots"][0]["status"] == "done"


def test_summary_says_normal_when_nothing_is_pinned() -> None:
    zh, en = _summary([
        {"id": "RIL-AP-7004", "stats": {"pinned": 0.0}},
        {"id": "RIL-AP-7001", "idle": True},
        {"id": "10.111.87.27", "error": "连不上 10.111.87.27：端口 19208 拒绝了连接"},
    ])
    assert zh.startswith("RIL-AP-7004 正常。")
    assert "卡死" not in zh
    assert "没有加速度样本" not in zh
    assert "这段时间没有运动，patlog 未记录 IMU。" in zh
    assert "连不上 10.111.87.27：端口 19208 拒绝了连接。" in zh
    assert "RIL-AP-7004 is normal." in en


def test_summary_names_only_the_pinned_robots() -> None:
    zh, _en = _summary([
        {"id": "7004", "stats": {"pinned": 59.1}},
        {"id": "7005", "stats": {"pinned": 0.0}},
    ])
    assert zh == "7005 正常。7004 卡死 59.1%。"


def test_missing_imu_is_idle_not_a_failure(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        path = dest / "pat-2026-10-06-18-41-02.pat"
        path.write_bytes(b"")
        return [path]

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[{"id": "7004", "host": "10.0.0.4"}])
        started = client.post("/api/runs", json={"window": "latest"})
        run_id = started.json()["id"]
        deadline = time.time() + 10
        run = started.json()
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert run["robots"][0]["status"] == "idle"
        listed = client.get("/api/runs").json()["runs"]
        assert [item["id"] for item in listed] == [run_id]
        report = client.get(f"/api/runs/{run_id}/report").text
        assert "这段时间没有运动，patlog 未记录 IMU" in report
        assert "no ImuArray" not in report


def test_all_failed_run_is_left_out_of_history(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        raise RuntimeError("连不上")

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[{"id": "7004", "host": "10.0.0.4"}])
        started = client.post("/api/runs", json={"window": "latest"})
        run_id = started.json()["id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "failed"
        listed = client.get("/api/runs").json()["runs"]
        assert [item["id"] for item in listed] == []


def test_second_trigger_returns_the_run_in_progress(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    started = threading.Event()
    release = threading.Event()

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        started.set()
        assert release.wait(5)
        path = dest / "pat-2026-10-02-13-19-31.pat"
        write_pat(path, [(i * 100_000_000, 0.0, 0.0, 9.8) for i in range(8)])
        return [path]

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[
            {"id": "7004", "host": "10.0.0.4"},
            {"id": "7005", "host": "10.0.0.5"},
            {"id": "7006", "host": "10.0.0.6"},
        ])
        first = client.post("/api/runs", json={"window": "20m"})
        assert first.status_code == 200
        assert started.wait(5)
        second = client.post("/api/runs", json={"window": "20m"})
        assert second.json()["already_running"] is True
        assert second.json()["id"] == first.json()["id"]
        release.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{first.json()['id']}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"


def test_two_robots_at_a_time(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def fetcher(host, port, dest, *, last=None, start=None, end=None, latest=False, on_progress=None):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.4)
        with lock:
            state["current"] -= 1
        path = dest / "pat-2026-10-02-13-19-31.pat"
        write_pat(path, [(0, 0.0, 0.0, 9.8), (100_000_000, 0.0, 0.0, 9.8)])
        return [path]

    app.state.runner.fetcher = fetcher
    with TestClient(app) as client:
        client.put("/api/robots", json=[
            {"id": "7001", "host": "10.0.0.1"},
            {"id": "7002", "host": "10.0.0.2"},
            {"id": "7003", "host": "10.0.0.3"},
        ])
        started = client.post("/api/runs", json={"window": "10m"})
        run_id = started.json()["id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        assert run["source"] == "online"
        assert state["peak"] == 2


def test_lookup_fills_the_name_and_fixes_the_port(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("patlog_viewer.api.robot_name", lambda host: "RIL-AP-7004")
    with _client(tmp_path) as client:
        found = client.get("/api/robots/lookup", params={"host": "10.11.87.23"})
        assert found.status_code == 200
        assert found.json() == {"id": "RIL-AP-7004", "host": "10.11.87.23", "port": 19208}
        saved = client.put("/api/robots", json=[{"host": "10.11.87.23"}])
        assert saved.status_code == 200
        assert saved.json()["robots"] == [{"id": "RIL-AP-7004", "host": "10.11.87.23", "port": 19208}]


def test_unreachable_robot_is_kept_by_ip(tmp_path: Path, monkeypatch) -> None:
    def boom(host: str) -> str:
        raise ValueError(f"连不上 {host}")

    monkeypatch.setattr("patlog_viewer.api.robot_name", boom)
    with _client(tmp_path) as client:
        saved = client.put("/api/robots", json=[{"host": "10.11.87.23"}])
        assert saved.status_code == 200
        robot = saved.json()["robots"][0]
        assert robot["id"] == "10.11.87.23"
        assert robot["host"] == "10.11.87.23"
        found = client.get("/api/robots/lookup", params={"host": "10.11.87.23"})
        assert found.status_code == 200
        assert found.json()["pending"] is True


def test_delete_finished_run_removes_files(tmp_path: Path) -> None:
    folder = tmp_path / "7004"
    _pat(folder, "pat-2026-10-02-13-19-31.pat", 9.8)
    with _client(tmp_path) as client:
        started = client.post("/api/runs", json={"local_dirs": [{"id": "7004", "path": str(folder)}]})
        run_id = started.json()["id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "done"
        removed = client.delete(f"/api/runs/{run_id}")
        assert removed.status_code == 200
        assert client.get(f"/api/runs/{run_id}").status_code == 404
        assert not (tmp_path / "data" / "series" / run_id).exists()
        missing = client.delete(f"/api/runs/{run_id}")
        assert missing.status_code == 404


def test_schedule_tick_does_nothing_when_off(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    assert tick(app.state.runner) is None
    app.state.runner.db.set_schedule(True, 60, "20m")
    assert tick(app.state.runner) is None


def test_restart_keeps_the_saved_interval() -> None:
    now = datetime(2026, 10, 6, 18, 35, 12)
    anchor = datetime(2026, 10, 6, 18, 41, 0)
    assert keep_interval(anchor, 10, now) == anchor
    late = datetime(2026, 10, 6, 18, 45, 0)
    assert keep_interval(anchor, 10, late) == datetime(2026, 10, 6, 18, 51, 0)
    config = {
        "enabled": True,
        "mode": "interval",
        "interval_minutes": 10,
        "hour": 8,
        "minute": 0,
        "next_run": "2026-10-06 18:41:00",
    }
    assert resume_fire(config, now) == anchor


def test_next_fire_follows_the_clock() -> None:
    now = datetime(2026, 10, 6, 18, 13, 40)
    assert next_fire(
        {"enabled": True, "mode": "daily", "hour": 8, "minute": 30, "interval_minutes": 60}, now
    ) == datetime(2026, 10, 7, 8, 30)
    assert next_fire(
        {"enabled": True, "mode": "daily", "hour": 18, "minute": 30, "interval_minutes": 60}, now
    ) == datetime(2026, 10, 6, 18, 30)
    assert next_fire(
        {"enabled": True, "mode": "hourly", "hour": 8, "minute": 15, "interval_minutes": 60}, now
    ) == datetime(2026, 10, 6, 18, 15)
    assert next_fire(
        {"enabled": True, "mode": "hourly", "hour": 0, "minute": 0, "interval_minutes": 60}, now
    ) == datetime(2026, 10, 6, 19, 0)
    assert next_fire(
        {"enabled": False, "mode": "daily", "hour": 8, "minute": 0, "interval_minutes": 60}, now
    ) is None


def test_interval_rejects_less_than_ten_minutes(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    with TestClient(app) as client:
        low = client.put(
            "/api/schedule",
            json={"enabled": False, "interval_minutes": 9, "window": "latest", "mode": "interval"},
        )
        assert low.status_code == 400
        assert low.json()["detail"] == "间隔不能小于 10 分钟"
        ok = client.put(
            "/api/schedule",
            json={"enabled": False, "interval_minutes": 10, "window": "latest", "mode": "interval"},
        )
        assert ok.status_code == 200
        assert ok.json()["interval_minutes"] == 10


def test_clock_schedule_reports_next_run(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    with TestClient(app) as client:
        saved = client.put(
            "/api/schedule",
            json={
                "enabled": True,
                "interval_minutes": 60,
                "window": "latest",
                "mode": "daily",
                "hour": 8,
                "minute": 30,
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["mode"] == "daily"
        assert body["hour"] == 8
        assert body["minute"] == 30
        assert body["next_run"].endswith("08:30")
        again = client.get("/api/schedule").json()
        assert again["next_run"] == body["next_run"]
        hourly = client.put(
            "/api/schedule",
            json={"enabled": True, "window": "latest", "mode": "hourly", "minute": 15},
        )
        assert hourly.status_code == 200
        assert hourly.json()["mode"] == "hourly"
        assert hourly.json()["next_run"].endswith(":15")
        bad = client.put(
            "/api/schedule",
            json={"enabled": True, "window": "latest", "mode": "weekly"},
        )
        assert bad.status_code == 400
