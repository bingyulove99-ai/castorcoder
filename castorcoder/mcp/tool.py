"""Adapt MCP tools to CastorCoder's stable Tool protocol."""

from __future__ import annotations

import re
from typing import Any

from ..tools.base import Tool
from .client import MCPClient, MCPError

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_VALID_RISKS = {"read", "write", "execute", "delegate"}


class MCPTool(Tool):
    """A tool discovered from an MCP server."""

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        spec: dict[str, Any],
        *,
        risk: str = "execute",
    ) -> None:
        remote_name = str(spec.get("name", "tool"))
        self.remote_name = remote_name
        self.name = _safe_name(f"mcp_{server_name}_{remote_name}")
        self.description = str(
            spec.get("description")
            or f"MCP tool '{remote_name}' from server '{server_name}'."
        )
        schema = spec.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        self.parameters = schema
        self.risk = risk if risk in _VALID_RISKS else "execute"
        self.parallel_safe = False
        self.timeout_s = None
        self.client = client

    def execute(self, **kwargs) -> str:
        try:
            return self.client.call_tool(self.remote_name, kwargs)
        except MCPError as exc:
            return f"Error: {exc}"


class MCPManager:
    """Own the lifetime of several MCP servers and their adapted tools."""

    def __init__(
        self,
        servers: dict[str, dict[str, Any]],
        *,
        timeout: float = 20.0,
    ) -> None:
        self.servers = servers
        self.timeout = timeout
        self.clients: list[MCPClient] = []
        self._tools: list[MCPTool] = []

    def start(self) -> list[MCPTool]:
        try:
            for name, config in self.servers.items():
                command = config.get("command")
                if not command:
                    raise MCPError(f"MCP server '{name}' is missing command")
                client = MCPClient(
                    command,
                    args=list(config.get("args", [])),
                    env={str(key): str(value) for key, value in (config.get("env") or {}).items()},
                    cwd=config.get("cwd"),
                    timeout=float(config.get("timeout", self.timeout)),
                    name=name,
                )
                self.clients.append(client)
                client.start()
                risk = str(config.get("risk", "execute"))
                for spec in client.list_tools():
                    self._tools.append(MCPTool(client, name, spec, risk=risk))
        except Exception:
            self.close()
            raise
        return self._tools

    def tools(self) -> list[MCPTool]:
        return list(self._tools)

    def close(self) -> None:
        for client in self.clients:
            client.close()
        self.clients.clear()
        self._tools.clear()

    def __enter__(self) -> MCPManager:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME.sub("_", value).strip("_")
    return (cleaned or "mcp_tool")[:64]
