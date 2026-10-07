"""One run covers every robot. Two robots download at a time."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from patlog_viewer.db import Database
from patlog_viewer.fetch import fetch_pats, parse_window, pat_files, refresh_robot_names, robot_name
from patlog_viewer.parse import load_paths
from patlog_viewer.signals import series_document

TIME_FMT = "%Y-%m-%d %H:%M:%S"


class RunError(Exception):
    pass


class Runner:
    def __init__(self, db: Database, data_dir: Path, fetcher=None) -> None:
        self.db = db
        self.data_dir = data_dir
        self.fetcher = fetcher or fetch_pats
        self._lock = threading.Lock()
        self._active: str | None = None
        self._specs: dict[str, dict] = {}

    def recover(self) -> None:
        self.db.interrupt_running()

    def active_run(self) -> dict | None:
        with self._lock:
            run_id = self._active
        if run_id is None:
            return None
        return self.db.get_run(run_id)

    def start_remote(
        self,
        *,
        trigger: str,
        window: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[dict, bool]:
        robots = self.db.robots()
        if not robots:
            raise RunError("robot list is empty")
        with self._lock:
            busy = self._active is not None
        if not busy:
            robots, changed = refresh_robot_names(robots, robot_name)
            if changed:
                robots = self.db.set_robots(robots)
        if window is None and start is None and end is None:
            window = self.db.schedule()["window"]
        label, last, start_at, end_at = _remote_window(window, start, end)
        spec = {
            "kind": "remote",
            "last": last,
            "start": start_at,
            "end": end_at,
            "latest": window == "latest",
        }
        return self._launch(trigger, label, robots, spec, "online")

    def start_local(self, entries: list[dict], *, trigger: str = "manual") -> tuple[dict, bool]:
        if not entries:
            raise RunError("no local patlog directories")
        robots = []
        for item in entries:
            folder = Path(item["path"])
            if not folder.is_dir():
                raise RunError(f"directory not found: {folder}")
            robots.append({"id": item["id"], "host": str(folder), "path": folder})
        spec = {"kind": "local", "paths": {item["id"]: item["path"] for item in robots}}
        return self._launch(trigger, "local", robots, spec, "local")

    def _launch(
        self,
        trigger: str,
        label: str,
        robots: list[dict],
        spec: dict,
        source: str,
    ) -> tuple[dict, bool]:
        with self._lock:
            if self._active is not None:
                current = self.db.get_run(self._active)
                assert current is not None
                return current, True
            run_id = self._time_id()
            stored = [{"id": item["id"], "host": item.get("host")} for item in robots]
            run = self.db.create_run(run_id, trigger, label, stored, source)
            self._active = run_id
            self._specs[run_id] = spec
        thread = threading.Thread(target=self._execute, args=(run_id,), name=f"run-{run_id}", daemon=True)
        thread.start()
        return run, False

    def _time_id(self) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = stamp
        suffix = 2
        while self.db.has_run(candidate):
            candidate = f"{stamp}_{suffix}"
            suffix += 1
        return candidate

    def _execute(self, run_id: str) -> None:
        spec = self._specs.get(run_id, {})
        run = self.db.get_run(run_id)
        assert run is not None
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(self._one, run_id, robot["id"], spec)
                    for robot in run["robots"]
                ]
                for future in futures:
                    future.result()
            finished = self.db.get_run(run_id)
            assert finished is not None
            ok = any(robot["status"] in {"done", "idle"} for robot in finished["robots"])
            self.db.finish_run(run_id, "done" if ok else "failed", None if ok else "every robot failed")
        except Exception as exc:  # noqa: BLE001
            self.db.finish_run(run_id, "failed", str(exc))
        finally:
            with self._lock:
                if self._active == run_id:
                    self._active = None
                self._specs.pop(run_id, None)

    def _one(self, run_id: str, robot_id: str, spec: dict) -> None:
        raw_dir = self.data_dir / "raw" / run_id / _safe(robot_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            if spec.get("kind") == "local":
                folder = Path(spec["paths"][robot_id])
                sources = pat_files(folder)
            else:
                self.db.set_robot(run_id, robot_id, "connecting")
                run = self.db.get_run(run_id)
                assert run is not None
                robot = next(item for item in run["robots"] if item["id"] == robot_id)
                host = robot.get("host") or ""
                port = 19208
                for listed in self.db.robots():
                    if listed["id"] == robot_id:
                        host = listed["host"]
                        port = int(listed["port"])
                        break
                downloading = False

                def on_progress(received: int, total: int) -> None:
                    nonlocal downloading
                    if not downloading:
                        self.db.set_robot(run_id, robot_id, "downloading")
                        downloading = True
                    self.db.set_download_progress(run_id, robot_id, received, total)

                sources = self.fetcher(
                    host,
                    port,
                    raw_dir,
                    last=spec.get("last"),
                    start=spec.get("start"),
                    end=spec.get("end"),
                    latest=bool(spec.get("latest")),
                    on_progress=on_progress,
                )
            if not sources:
                raise RunError("no patlog in window")
            self.db.set_robot(run_id, robot_id, "parsing")
            rows, notes = load_paths(list(sources))
            if not rows:
                if notes:
                    raise RunError("; ".join(notes))
                (raw_dir / "sources.json").write_text(
                    json.dumps([str(path) for path in sources], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.db.set_robot(run_id, robot_id, "idle")
                return
            document = series_document(robot_id, rows, [str(path) for path in sources])
            if notes:
                document["notes"] = notes
            _write_json(self.data_dir / "series" / run_id / f"{_safe(robot_id)}.json", document)
            (raw_dir / "sources.json").write_text(
                json.dumps([str(path) for path in sources], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.db.set_robot(run_id, robot_id, "done")
        except Exception as exc:  # noqa: BLE001
            self.db.set_robot(run_id, robot_id, "failed", str(exc))


def load_series(data_dir: Path, run_id: str, robot_id: str) -> dict | None:
    path = data_dir / "series" / run_id / f"{_safe(robot_id)}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _safe(robot_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in robot_id)


def _remote_window(
    window: str | None,
    start: str | None,
    end: str | None,
) -> tuple[str, timedelta | None, datetime | None, datetime | None]:
    if window and (start or end):
        raise RunError("pass a duration or an absolute window, not both")
    if window == "latest":
        return "latest file", None, None, None
    if window:
        return f"last {window}", parse_window(window), None, None
    if (start is None) != (end is None):
        raise RunError("pass both start and end")
    if start and end:
        start_at = datetime.strptime(start, TIME_FMT)
        end_at = datetime.strptime(end, TIME_FMT)
        if start_at >= end_at:
            raise RunError("empty window")
        return f"{start}–{end}", None, start_at, end_at
    raise RunError("pass a window")
