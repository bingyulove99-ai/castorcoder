"""Minimal MCP stdio client tests against an in-repo fake server."""

from __future__ import annotations

import sys

from castorcoder.mcp import MCPClient, MCPError, MCPManager

SERVER = r'''
import json
import sys

def send(message):
    print(json.dumps(message), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "fake", "version": "1"},
            "capabilities": {"tools": {}},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [
            {"name": "echo", "description": "Echo text", "inputSchema": {
                "type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"], "additionalProperties": False}},
            {"name": "boom", "description": "Fail", "inputSchema": {
                "type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        name = message["params"]["name"]
        arguments = message["params"]["arguments"]
        if name == "boom":
            send({"jsonrpc": "2.0", "id": message["id"], "result": {
                "isError": True, "content": [{"type": "text", "text": "boom"}]}})
        else:
            send({"jsonrpc": "2.0", "id": message["id"], "result": {
                "content": [{"type": "text", "text": "echo:" + arguments.get("text", "")}]}})
'''


def _server(tmp_path):
    path = tmp_path / "fake_server.py"
    path.write_text(SERVER, encoding="utf-8")
    return path


def test_mcp_client_initializes_lists_and_calls(tmp_path):
    client = MCPClient([sys.executable, str(_server(tmp_path))], name="fake")
    try:
        info = client.start()
        assert info["name"] == "fake"
        tools = client.list_tools()
        assert {tool["name"] for tool in tools} == {"echo", "boom"}
        assert client.call_tool("echo", {"text": "hello"}) == "echo:hello"
        assert client.call_tool("boom", {}) == "Error: boom"
    finally:
        client.close()


def test_mcp_manager_adapts_tools_and_risk(tmp_path):
    manager = MCPManager(
        {
            "fake": {
                "command": sys.executable,
                "args": [str(_server(tmp_path))],
                "risk": "read",
            }
        }
    )
    try:
        tools = manager.start()
        assert {tool.name for tool in tools} == {"mcp_fake_echo", "mcp_fake_boom"}
        echo = next(tool for tool in tools if tool.remote_name == "echo")
        assert echo.risk == "read"
        assert echo.parameters["properties"]["text"]["type"] == "string"
        assert echo.execute(text="hi") == "echo:hi"
    finally:
        manager.close()


def test_mcp_manager_rejects_bad_configuration():
    manager = MCPManager({"broken": {}})
    try:
        manager.start()
    except MCPError as exc:
        assert "command" in str(exc)
    else:
        raise AssertionError("missing command was accepted")


def test_mcp_client_times_out_when_server_never_answers(tmp_path):
    server = tmp_path / "silent.py"
    server.write_text("import time\nwhile True:\n    time.sleep(0.1)\n", encoding="utf-8")
    client = MCPClient(
        [sys.executable, str(server)],
        name="silent",
        timeout=0.2,
    )
    try:
        try:
            client.start()
        except MCPError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("silent server did not time out")
    finally:
        client.close()


def test_mcp_manager_reports_spawn_failure(tmp_path):
    manager = MCPManager(
        {"broken": {"command": str(tmp_path / "does-not-exist")}}
    )
    try:
        manager.start()
    except MCPError as exc:
        assert "failed to start" in str(exc)
    else:
        raise AssertionError("missing executable was accepted")
