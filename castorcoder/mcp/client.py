"""A minimal, dependency-free MCP client over stdio JSON-RPC."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """Raised when an MCP server fails, times out or returns an error."""


class MCPClient:
    """Start one MCP server and expose ``list_tools``/``call_tool``."""

    def __init__(
        self,
        command: str | list[str],
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout: float = 20.0,
        name: str = "mcp",
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = Path(cwd) if cwd else None
        self.timeout = timeout
        self.name = name
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}

        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: list[str] = []
        self._stderr_lock = threading.Lock()
        self._send_lock = threading.RLock()
        self._next_id = 0
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> dict[str, Any]:
        if self._process is not None:
            return self.server_info
        command = (
            [self.command, *self.args]
            if isinstance(self.command, str)
            else [*self.command, *self.args]
        )
        environment = os.environ.copy()
        environment.update(self.env)
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self.cwd) if self.cwd else None,
                env=environment,
            )
        except OSError as exc:
            raise MCPError(f"failed to start MCP server '{self.name}': {exc}") from exc

        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()

        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "castorcoder", "version": "1.3.0"},
            },
        )
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self.capabilities = result.get("capabilities", {}) if isinstance(result, dict) else {}
        self.notify("notifications/initialized", {})
        return self.server_info

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self._process is None:
            raise MCPError("MCP client is not started")
        with self._send_lock:
            self._next_id += 1
            request_id = self._next_id
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP server '{self.name}' timed out on {method}. "
                    f"stderr: {self.stderr_tail()}"
                )
            try:
                message = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise MCPError(
                    f"MCP server '{self.name}' timed out on {method}. "
                    f"stderr: {self.stderr_tail()}"
                ) from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise MCPError(
                    f"MCP server '{self.name}' returned an error for {method}: {error}"
                )
            return message.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if not isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        content = result.get("content", [])
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, dict):
                    parts.append(json.dumps(item, ensure_ascii=False))
        text = "\n".join(part for part in parts if part) or json.dumps(result, ensure_ascii=False)
        return f"Error: {text}" if result.get("isError") else text

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()

    def stderr_tail(self, limit: int = 2_000) -> str:
        with self._stderr_lock:
            return "".join(self._stderr)[-limit:]

    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError("MCP client is not started")
        with self._send_lock:
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPError(f"MCP server '{self.name}' closed its stdin: {exc}") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") is not None:
                self._responses.put(message)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            with self._stderr_lock:
                self._stderr.append(line)
                if len(self._stderr) > 200:
                    del self._stderr[:100]
