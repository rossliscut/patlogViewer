"""SQLite store for robots, the schedule, and runs."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS robots (
              id TEXT PRIMARY KEY,
              host TEXT NOT NULL,
              port INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              enabled INTEGER NOT NULL,
              interval_minutes INTEGER NOT NULL,
              window TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              trigger TEXT NOT NULL,
              status TEXT NOT NULL,
              window_label TEXT NOT NULL,
              created_at TEXT NOT NULL,
              finished_at TEXT,
              error TEXT
            );
            CREATE TABLE IF NOT EXISTS run_robots (
              run_id TEXT NOT NULL,
              robot_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              host TEXT,
              PRIMARY KEY (run_id, robot_id)
            );
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO schedule (id, enabled, interval_minutes, window) VALUES (1, 0, 60, 'latest')"
        )
        schedule_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(schedule)")}
        if "mode" not in schedule_columns:
            self._conn.execute("ALTER TABLE schedule ADD COLUMN mode TEXT NOT NULL DEFAULT 'interval'")
        if "hour" not in schedule_columns:
            self._conn.execute("ALTER TABLE schedule ADD COLUMN hour INTEGER NOT NULL DEFAULT 8")
        if "minute" not in schedule_columns:
            self._conn.execute("ALTER TABLE schedule ADD COLUMN minute INTEGER NOT NULL DEFAULT 0")
        if "next_run" not in schedule_columns:
            self._conn.execute("ALTER TABLE schedule ADD COLUMN next_run TEXT")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)")}
        if "source" not in columns:
            self._conn.execute("ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'online'")
            self._conn.execute("UPDATE runs SET source = 'local' WHERE window_label = 'local'")
        robot_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(run_robots)")}
        if "received" not in robot_columns:
            self._conn.execute("ALTER TABLE run_robots ADD COLUMN received INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("ALTER TABLE run_robots ADD COLUMN total INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            found = self._conn.execute(sql, args).fetchall()
        return [dict(row) for row in found]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    def robots(self) -> list[dict]:
        return self._rows("SELECT id, host, port FROM robots ORDER BY id")

    def set_robots(self, robots: list[dict]) -> list[dict]:
        with self._lock:
            self._conn.execute("DELETE FROM robots")
            self._conn.executemany(
                "INSERT INTO robots (id, host, port) VALUES (?, ?, ?)",
                [(item["id"], item["host"], int(item["port"])) for item in robots],
            )
            self._conn.commit()
        return self.robots()

    def schedule(self) -> dict:
        row = self._one(
            "SELECT enabled, interval_minutes, window, mode, hour, minute, next_run FROM schedule WHERE id = 1"
        )
        assert row is not None
        row["enabled"] = bool(row["enabled"])
        row["hour"] = int(row["hour"])
        row["minute"] = int(row["minute"])
        return row

    def set_schedule(
        self,
        enabled: bool,
        interval_minutes: int,
        window: str,
        mode: str = "interval",
        hour: int = 8,
        minute: int = 0,
    ) -> dict:
        with self._lock:
            self._conn.execute(
                """
                UPDATE schedule
                SET enabled = ?, interval_minutes = ?, window = ?, mode = ?, hour = ?, minute = ?
                WHERE id = 1
                """,
                (1 if enabled else 0, interval_minutes, window, mode, hour, minute),
            )
            self._conn.commit()
        return self.schedule()

    def set_next_run(self, when: datetime | None) -> None:
        text = when.strftime("%Y-%m-%d %H:%M:%S") if when else None
        with self._lock:
            self._conn.execute("UPDATE schedule SET next_run = ? WHERE id = 1", (text,))
            self._conn.commit()

    def create_run(
        self,
        run_id: str,
        trigger: str,
        window_label: str,
        robots: list[dict],
        source: str,
    ) -> dict:
        created = now_text()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs (id, trigger, status, window_label, created_at, source)
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (run_id, trigger, window_label, created, source),
            )
            self._conn.executemany(
                """
                INSERT INTO run_robots (run_id, robot_id, position, status, host)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                [
                    (run_id, item["id"], index, item.get("host"))
                    for index, item in enumerate(robots)
                ],
            )
            self._conn.commit()
        found = self.get_run(run_id)
        assert found is not None
        return found

    def has_run(self, run_id: str) -> bool:
        return self._one("SELECT id FROM runs WHERE id = ?", (run_id,)) is not None

    def set_download_progress(self, run_id: str, robot_id: str, received: int, total: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE run_robots SET received = ?, total = ? WHERE run_id = ? AND robot_id = ?",
                (received, total, run_id, robot_id),
            )
            self._conn.commit()

    def set_robot(self, run_id: str, robot_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE run_robots SET status = ?, error = ? WHERE run_id = ? AND robot_id = ?",
                (status, error, run_id, robot_id),
            )
            self._conn.commit()

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, now_text(), error, run_id),
            )
            self._conn.commit()

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM run_robots WHERE run_id = ?", (run_id,))
            self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self._conn.commit()

    def interrupt_running(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE runs
                SET status = 'failed', finished_at = ?, error = 'interrupted'
                WHERE status = 'running'
                """,
                (now_text(),),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        run = self._one(
            "SELECT id, trigger, status, window_label, created_at, finished_at, error, source FROM runs WHERE id = ?",
            (run_id,),
        )
        if run is None:
            return None
        run["robots"] = self._rows(
            """
            SELECT robot_id AS id, status, error, host, received, total
            FROM run_robots
            WHERE run_id = ?
            ORDER BY position
            """,
            (run_id,),
        )
        return run

    def list_runs(self) -> list[dict]:
        rows = self._rows(
            """
            SELECT id, trigger, status, window_label, created_at, finished_at, error, source
            FROM runs
            WHERE status != 'failed'
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        for run in rows:
            run["robots"] = self._rows(
                """
                SELECT robot_id AS id, status, error, host, received, total
                FROM run_robots
                WHERE run_id = ?
                ORDER BY position
                """,
                (run["id"],),
            )
        return rows
