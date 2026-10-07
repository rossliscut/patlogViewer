"""Run the viewer on this machine only."""

from __future__ import annotations

import uvicorn

from patlog_viewer.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
