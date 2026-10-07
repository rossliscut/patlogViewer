"""FastAPI app. The page, the API, and the scheduler live in one process."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from patlog_viewer.api import router
from patlog_viewer.db import Database
from patlog_viewer.jobs import Runner
from patlog_viewer.schedule import start_scheduler

STATIC = Path(__file__).resolve().parent / "static"


def default_data_dir() -> Path:
    env = os.environ.get("PATLOG_DATA")
    if env:
        return Path(env)
    return Path.cwd() / "data"


def create_app(data_dir: Path | None = None, *, start_scheduler_flag: bool = True) -> FastAPI:
    data = Path(data_dir) if data_dir is not None else default_data_dir()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if app.state.start_scheduler_flag:
            scheduler = start_scheduler(app.state.runner)
            app.state.scheduler = scheduler
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Patlog Viewer", lifespan=lifespan)
    app.state.start_scheduler_flag = start_scheduler_flag
    app.state.runner = Runner(Database(data / "patlog.sqlite"), data)
    app.state.runner.recover()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    return app
