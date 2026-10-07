"""HTTP API shared by the page and the MCP server."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from patlog_viewer import __version__, built_at
from patlog_viewer.fetch import ROBOT_PORT, discover_local, robot_name
from patlog_viewer.jobs import RunError, load_series
from patlog_viewer.report import build_report
from patlog_viewer.schedule import apply_schedule, next_fire
from patlog_viewer.signals import SIGNALS

router = APIRouter()
TIME_FMT = "%Y-%m-%d %H:%M:%S"
WINDOWS = ("latest", "10m", "20m", "30m")


class RobotIn(BaseModel):
    id: str = ""
    host: str
    port: int = ROBOT_PORT


class ScheduleIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=60, ge=1, le=24 * 60)
    window: str
    mode: str = "interval"
    hour: int = Field(default=8, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class LocalDir(BaseModel):
    id: str
    path: str


class RunIn(BaseModel):
    window: str | None = None
    start: str | None = None
    end: str | None = None
    local_dirs: list[LocalDir] | None = None
    local_root: str | None = None


def _runner(request: Request):
    return request.app.state.runner


def _check_id(robot_id: str) -> str:
    text = robot_id.strip()
    if not text or "/" in text or "\\" in text:
        raise HTTPException(400, "robot id is empty or contains a path separator")
    return text


@router.get("/api/version")
def get_version(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"version": __version__, "built_at": built_at()}


@router.get("/api/signals")
def list_signals() -> dict:
    return {"signals": SIGNALS}


@router.get("/api/robots")
def get_robots(request: Request) -> dict:
    return {"robots": _runner(request).db.robots()}


def _named_robot(item: RobotIn) -> dict:
    host = item.host.strip()
    if not host:
        raise HTTPException(400, "需要 IP")
    robot_id = item.id.strip()
    pending = False
    if not robot_id or robot_id == host:
        try:
            robot_id = robot_name(host)
        except ValueError:
            robot_id = host
            pending = True
    robot = {"id": _check_id(robot_id), "host": host, "port": ROBOT_PORT}
    if pending:
        robot["pending"] = True
    return robot


@router.get("/api/robots/lookup")
def lookup_robot(host: str) -> dict:
    return _named_robot(RobotIn(host=host))


@router.put("/api/robots")
def put_robots(body: list[RobotIn], request: Request) -> dict:
    cleaned = []
    seen_ids: set[str] = set()
    seen_hosts: set[str] = set()
    for item in body:
        robot = _named_robot(item)
        if robot["host"] in seen_hosts:
            raise HTTPException(400, f"{robot['host']} 已经在列表里")
        if robot["id"] in seen_ids:
            raise HTTPException(400, f"{robot['id']} 已经在列表里")
        seen_hosts.add(robot["host"])
        seen_ids.add(robot["id"])
        cleaned.append(robot)
    return {"robots": _runner(request).db.set_robots(cleaned)}


def _schedule_view(request: Request) -> dict:
    runner = _runner(request)
    saved = runner.db.schedule()
    when = None
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        job = scheduler.get_job("patlog-fetch")
        if job is not None and job.next_run_time is not None:
            when = job.next_run_time
            if when.tzinfo is not None:
                when = when.astimezone().replace(tzinfo=None)
    if when is None:
        when = next_fire(saved)
    saved["next_run"] = when.strftime("%Y-%m-%d %H:%M") if when else None
    return saved


@router.get("/api/schedule")
def get_schedule(request: Request) -> dict:
    return _schedule_view(request)


def _window(text: str) -> str:
    text = text.strip()
    if text not in WINDOWS:
        raise HTTPException(400, "window must be latest, 10m, 20m, or 30m")
    return text


def _mode(text: str) -> str:
    if text not in {"interval", "daily", "hourly"}:
        raise HTTPException(400, "定时方式要是每隔、每天或每小时")
    return text


@router.put("/api/schedule")
def put_schedule(body: ScheduleIn, request: Request) -> dict:
    window = _window(body.window)
    mode = _mode(body.mode)
    if mode == "interval" and body.interval_minutes < 10:
        raise HTTPException(400, "间隔不能小于 10 分钟")
    runner = _runner(request)
    runner.db.set_schedule(body.enabled, body.interval_minutes, window, mode, body.hour, body.minute)
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        apply_schedule(scheduler, runner, reschedule=True)
    return _schedule_view(request)


@router.post("/api/runs")
def post_run(body: RunIn, request: Request) -> dict:
    runner = _runner(request)
    try:
        if body.local_root and body.local_dirs:
            raise RunError("pass local_root or local_dirs, not both")
        if body.local_root or body.local_dirs:
            if body.local_root:
                entries = discover_local(Path(body.local_root))
            else:
                entries = [{"id": _check_id(item.id), "path": item.path} for item in body.local_dirs or []]
            run, already = runner.start_local(entries, trigger="manual")
        else:
            if body.start or body.end:
                _parse_clock(body.start, "start")
                _parse_clock(body.end, "end")
            window = _window(body.window) if body.window else None
            run, already = runner.start_remote(
                trigger="manual",
                window=window,
                start=body.start,
                end=body.end,
            )
    except RunError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"already_running": already, **run}


@router.get("/api/runs")
def get_runs(request: Request) -> dict:
    return {"runs": _runner(request).db.list_runs()}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    run = _runner(request).db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


def _safe_run_id(run_id: str) -> str:
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise HTTPException(400, "bad run id")
    return run_id


def _zip_tree(archive: zipfile.ZipFile, folder: Path, prefix: str, seen: set[Path]) -> None:
    if not folder.is_dir():
        return
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        archive.write(resolved, arcname=prefix + path.relative_to(folder).as_posix())
        seen.add(resolved)


def _zip_source_files(archive: zipfile.ZipFile, raw: Path, seen: set[Path]) -> None:
    if not raw.is_dir():
        return
    for spec in raw.rglob("sources.json"):
        try:
            listed = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        robot = spec.parent.name
        for item in listed:
            path = Path(str(item))
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            archive.write(resolved, arcname=f"raw/{robot}/{path.name}")
            seen.add(resolved)


@router.get("/api/runs/{run_id}/export")
def export_run(run_id: str, request: Request) -> FileResponse:
    run_id = _safe_run_id(run_id)
    runner = _runner(request)
    run = runner.db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    handle = tempfile.NamedTemporaryFile(prefix=f"patlog-{run_id}-", suffix=".zip", delete=False)
    handle.close()
    archive_path = Path(handle.name)
    seen: set[Path] = set()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("run.json", json.dumps(run, ensure_ascii=False, indent=2))
        _zip_tree(archive, runner.data_dir / "raw" / run_id, "raw/", seen)
        _zip_tree(archive, runner.data_dir / "series" / run_id, "series/", seen)
        _zip_source_files(archive, runner.data_dir / "raw" / run_id, seen)
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"patlog-{run_id}.zip",
        background=BackgroundTask(archive_path.unlink),
    )


@router.delete("/api/runs/{run_id}")
def delete_run(run_id: str, request: Request) -> dict:
    run_id = _safe_run_id(run_id)
    runner = _runner(request)
    run = runner.db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] == "running":
        raise HTTPException(409, "run is still running")
    runner.db.delete_run(run_id)
    for folder in (runner.data_dir / "raw" / run_id, runner.data_dir / "series" / run_id):
        if folder.is_dir():
            shutil.rmtree(folder)
    report = runner.data_dir / "reports" / f"{run_id}.html"
    if report.is_file():
        report.unlink()
    return {"deleted": run_id}


@router.get("/api/runs/{run_id}/series")
def get_series(run_id: str, request: Request, signal: str = "imu.acc") -> dict:
    if signal != "imu.acc":
        raise HTTPException(404, f"unknown signal {signal}")
    runner = _runner(request)
    run = runner.db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    panels = []
    for robot in run["robots"]:
        document = load_series(runner.data_dir, run_id, robot["id"])
        if document is None:
            panel = {"id": robot["id"], "error": robot.get("error"), "t": [], "ax": [], "ay": [], "az": []}
            if robot.get("status") == "idle":
                panel["idle"] = True
            panels.append(panel)
        else:
            panels.append(document)
    return {"signal": signal, "run": run, "panels": panels}


@router.get("/api/runs/{run_id}/report")
def get_report(run_id: str, request: Request) -> HTMLResponse:
    runner = _runner(request)
    run = runner.db.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] == "running":
        raise HTTPException(409, "run is still going")
    series = get_series(run_id, request)
    html = build_report(run, series["panels"])
    path = runner.data_dir / "reports" / f"{run_id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return HTMLResponse(html)


def _parse_clock(value: str | None, label: str) -> None:
    if value is None:
        return
    try:
        datetime.strptime(value, TIME_FMT)
    except ValueError as exc:
        raise HTTPException(400, f"{label} must be {TIME_FMT}") from exc
