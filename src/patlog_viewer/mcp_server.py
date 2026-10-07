"""stdio MCP server. Every tool calls the running HTTP service."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from patlog_viewer import __version__

DEFAULT_URL = "http://127.0.0.1:8765"

TOOLS = [
    {
        "name": "list_robots",
        "description": "列出已保存的机器人名称和 IP。端口固定为 19208。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_robots",
        "description": "用这份列表替换机器人。每条给出 host（IP）。名称 id 可省略，省略时从机器人读取。端口固定为 19208。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "robots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "host": {"type": "string"},
                        },
                        "required": ["host"],
                    },
                }
            },
            "required": ["robots"],
        },
    },
    {
        "name": "get_schedule",
        "description": "读取定时获取的开关、方式、钟点和窗口，以及下次执行时间。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_schedule",
        "description": "设置定时获取。mode 为 interval（每隔 interval_minutes 分钟，至少 10）、daily（每天 hour:minute）或 hourly（每小时的 minute 分）。window 可以是 latest、10m、20m 或 30m。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "interval_minutes": {"type": "integer"},
                "window": {"type": "string"},
                "mode": {"type": "string"},
                "hour": {"type": "integer"},
                "minute": {"type": "integer"},
            },
            "required": ["enabled", "window"],
        },
    },
    {
        "name": "start_run",
        "description": "人工开始一次任务。可给 window、start+end，或 local_root / local_dirs 直接解析本机 patlog。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "local_root": {"type": "string"},
                "local_dirs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "path": {"type": "string"}},
                        "required": ["id", "path"],
                    },
                },
            },
        },
    },
    {
        "name": "list_runs",
        "description": "列出最近的任务。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_run",
        "description": "读取一个任务和每台机器人的状态。",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "list_signals",
        "description": "列出可画的信号。第一期是 imu.acc。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_series",
        "description": "取一个任务的三轴加速度曲线和统计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "signal": {"type": "string"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_report",
        "description": "把一个已结束的任务导出成自包含 HTML 报告，并返回 HTML。",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
]


def http_json(method: str, url: str, body: dict | list | None = None):
    data = None
    headers = {"Accept": "application/json, text/html"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {detail}") from exc
    text = raw.decode("utf-8")
    if "application/json" in content_type or text[:1] in "{[":
        return json.loads(text)
    return text


def call_tool(name: str, arguments: dict, base_url: str, transport=http_json):
    root = base_url.rstrip("/")
    args = arguments or {}
    if name == "list_robots":
        return transport("GET", f"{root}/api/robots")
    if name == "set_robots":
        return transport("PUT", f"{root}/api/robots", args.get("robots", []))
    if name == "get_schedule":
        return transport("GET", f"{root}/api/schedule")
    if name == "set_schedule":
        return transport("PUT", f"{root}/api/schedule", args)
    if name == "start_run":
        return transport("POST", f"{root}/api/runs", args)
    if name == "list_runs":
        return transport("GET", f"{root}/api/runs")
    if name == "get_run":
        return transport("GET", f"{root}/api/runs/{args['run_id']}")
    if name == "list_signals":
        return transport("GET", f"{root}/api/signals")
    if name == "get_series":
        signal = args.get("signal") or "imu.acc"
        return transport("GET", f"{root}/api/runs/{args['run_id']}/series?signal={signal}")
    if name == "get_report":
        return transport("GET", f"{root}/api/runs/{args['run_id']}/report")
    raise RuntimeError(f"unknown tool {name}")


def handle(message: dict, base_url: str = DEFAULT_URL, transport=http_json) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "notifications/initialized" or method and method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None
    try:
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
            result = {
                "protocolVersion": requested,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "patlog-viewer", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            try:
                payload = call_tool(params.get("name", ""), params.get("arguments") or {}, base_url, transport)
                text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            except Exception as exc:  # noqa: BLE001
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            return _error(msg_id, -32601, f"unknown method {method}")
    except Exception as exc:  # noqa: BLE001
        return _error(msg_id, -32000, str(exc))
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("ascii", errors="replace")
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main() -> None:
    base = os.environ.get("PATLOG_VIEWER_URL", DEFAULT_URL)
    while True:
        message = _read_message()
        if message is None:
            return
        response = handle(message, base)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    main()
