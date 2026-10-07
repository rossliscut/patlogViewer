from patlog_viewer.mcp_server import TOOLS, handle


def test_initialize_lists_report_tool() -> None:
    ready = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert ready["result"]["serverInfo"]["name"] == "patlog-viewer"
    assert ready["result"]["protocolVersion"] == "2024-11-05"
    listing = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert names == {tool["name"] for tool in TOOLS}
    assert "get_report" in names
    assert "start_run" in names
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tool_call_uses_the_http_api() -> None:
    seen = {}

    def transport(method, url, body=None):
        seen["method"] = method
        seen["url"] = url
        seen["body"] = body
        return {"signals": [{"id": "imu.acc"}]}

    response = handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_signals", "arguments": {}}},
        transport=transport,
    )
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/api/signals")
    assert response["result"]["isError"] is False
    assert "imu.acc" in response["result"]["content"][0]["text"]
