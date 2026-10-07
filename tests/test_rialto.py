import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from patlog_viewer.app import create_app

RIALTO = Path(os.environ.get("RIALTO_PATLOG_DIR", r"C:\Users\rossl\Downloads\Rialto Misloc"))
TASKCHAIN = RIALTO / "taskchain"
BOT_7005 = RIALTO / "7005"


pytestmark = pytest.mark.skipif(
    not (TASKCHAIN / "pat-2026-10-02-13-19-31.pat").is_file() or not any(BOT_7005.glob("*.pat")),
    reason="Rialto patlogs are not on this machine",
)


def test_rialto_patlogs_match_the_known_az_numbers(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data", start_scheduler_flag=False)
    with TestClient(app) as client:
        started = client.post(
            "/api/runs",
            json={
                "local_dirs": [
                    {"id": "7004", "path": str(TASKCHAIN)},
                    {"id": "7005", "path": str(BOT_7005)},
                ]
            },
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]
        deadline = time.time() + 180
        run = None
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.2)
        assert run is not None and run["status"] == "done", run
        series = client.get(f"/api/runs/{run_id}/series", params={"signal": "imu.acc"}).json()
        by_id = {panel["id"]: panel for panel in series["panels"]}
        stuck = by_id["7004"]["stats"]
        healthy = by_id["7005"]["stats"]
        assert stuck["n"] == 13936
        assert stuck["az"]["median"] == pytest.approx(-39.127, abs=0.0005)
        assert stuck["pinned"] == pytest.approx(59.1, abs=0.15)
        assert stuck["span"].startswith("2026-10-02 13:19")
        assert healthy["n"] == 10392
        assert healthy["az"]["median"] == pytest.approx(9.780, abs=0.0005)
        assert healthy["pinned"] == pytest.approx(0.0, abs=0.05)
        assert healthy["span"].startswith("2026-10-02 13:56")
        for panel in (by_id["7004"], by_id["7005"]):
            assert len(panel["ax"]) == len(panel["ay"]) == len(panel["az"]) == panel["stats"]["n"]
        report = client.get(f"/api/runs/{run_id}/report")
        assert report.status_code == 200
        html = report.text
        assert "13936" in html and "10392" in html
        assert "-39.127" in html and "+9.780" in html
        assert "时间约定" in html and "Time convention" in html
        assert "ax" in html and "ay" in html and "az" in html
