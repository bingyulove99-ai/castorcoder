"""Zero-dependency local developer dashboard and trace waterfall cockpit for CastorCoder.

Inspired by waku-agent's dashboard:
- Built strictly on Python's stdlib http.server (no npm/Node, no external heavy frameworks).
- Serves an interactive developer cockpit:
  - Trace Waterfall: Inspect rounds, tool calls, latencies, tokens, and outputs.
  - Memory Inspector: Search, view, and manage workspace long-term memories.
  - Release Gate & Evals: Review quality checks, benchmarks, and pass@1 stats.
  - System Overview: Active model, workspace, and spend summary.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import __version__
from ..metrics import UsageLedger
from ..session import list_sessions
from .page import DASHBOARD_HTML

DEFAULT_PORT = 7777
MAX_TRACE_BYTES = 8 * 1024 * 1024


def _read_trace_events(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL trace, tolerating truncated or corrupt lines."""

    try:
        if path.stat().st_size > MAX_TRACE_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Condense one run's events into the numbers the trace list shows."""

    summary: dict[str, Any] = {
        "started_at": 0.0,
        "duration_ms": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tool_calls": 0,
        "failures": 0,
        "stop_reason": "",
        "model": "",
    }
    if not events:
        return summary
    started = events[0].get("timestamp")
    ended = events[-1].get("timestamp")
    try:
        summary["started_at"] = float(started or 0.0)
        if started and ended:
            summary["duration_ms"] = max(0.0, (float(ended) - float(started)) * 1000)
    except (TypeError, ValueError):
        pass
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        kind = event.get("type")
        if kind == "llm.completed":
            summary["tokens_in"] += _as_int(data.get("prompt_tokens"))
            summary["tokens_out"] += _as_int(data.get("completion_tokens"))
        elif kind == "tool.completed":
            summary["tool_calls"] += 1
            if data.get("success") is False:
                summary["failures"] += 1
        elif kind == "run.started" and not summary["model"]:
            summary["model"] = str(data.get("model") or "")
        elif kind == "run.completed" and not summary["stop_reason"]:
            stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
            summary["stop_reason"] = str(
                data.get("stop_reason") or stats.get("stop_reason") or ""
            )
    return summary


def _recent_usage(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    """Return the newest ledger rows with only the fields the UI renders."""

    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        rows.append({
            "ts": row.get("ts") or row.get("timestamp") or "",
            "model": row.get("model") or "",
            "prompt_tokens": _as_int(row.get("prompt_tokens")),
            "completion_tokens": _as_int(row.get("completion_tokens")),
            "rounds": _as_int(row.get("rounds")),
            "tool_calls": _as_int(row.get("tool_calls")),
            "tool_failures": _as_int(row.get("tool_failures")),
            "cost_usd": row.get("cost_usd") if isinstance(row.get("cost_usd"), (int, float)) else None,
            "stop_reason": row.get("stop_reason") or "",
        })
    return rows


def _last_model(path: Path) -> str:
    for row in reversed(_recent_usage(path, 50)):
        if row["model"]:
            return str(row["model"])
    return ""


class DashboardHandler(BaseHTTPRequestHandler):
    """Zero-dependency HTTP request handler for CastorCoder dashboard."""

    server_root: Path = Path.cwd()
    workspace_root: Path = Path.cwd()
    memory_db: Path | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy request logs."""
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_html(DASHBOARD_HTML)
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/traces":
            self._handle_traces_list()
        elif path.startswith("/api/traces/"):
            run_id = path[len("/api/traces/") :]
            self._handle_trace_detail(run_id)
        elif path == "/api/memories":
            self._handle_memories()
        elif path == "/api/sessions":
            self._handle_sessions()
        elif path == "/api/usage":
            self._handle_usage()
        elif path == "/api/gate":
            self._handle_gate()
        else:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _send_html(self, content: str) -> None:
        raw = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle_status(self) -> None:
        mem_count = 0
        if self.memory_db and self.memory_db.is_file():
            try:
                conn = sqlite3.connect(str(self.memory_db))
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM memories WHERE superseded_by IS NULL")
                mem_count = cur.fetchone()[0]
                conn.close()
            except Exception:
                mem_count = 0

        traces_dir = self.server_root / ".castorcoder" / "traces"
        trace_count = len(list(traces_dir.glob("*.jsonl"))) if traces_dir.is_dir() else 0

        sessions = list_sessions(limit=1000, workspace=self.workspace_root)
        ledger = UsageLedger()
        usage = ledger.summary()
        model = os.getenv("CASTORCODER_MODEL") or _last_model(ledger.path)
        if not model and traces_dir.is_dir():
            for path in sorted(traces_dir.glob("*.jsonl"), reverse=True)[:3]:
                model = str(_summarize_events(_read_trace_events(path))["model"] or "")
                if model:
                    break
        self._send_json({
            "version": __version__,
            "workspace": str(self.workspace_root),
            "memory_count": mem_count,
            "trace_count": trace_count,
            "session_count": len(sessions),
            "spend_usd": usage["cost_usd"],
            "model": model,
        })

    def _handle_sessions(self) -> None:
        self._send_json(list_sessions(limit=100, workspace=self.workspace_root))

    def _handle_usage(self) -> None:
        ledger = UsageLedger()
        summary = ledger.summary()
        summary["recent"] = _recent_usage(ledger.path, 12)
        self._send_json(summary)

    def _handle_traces_list(self) -> None:
        traces_dir = self.server_root / ".castorcoder" / "traces"
        result = []
        if traces_dir.is_dir():
            for p in sorted(traces_dir.glob("*.jsonl"), reverse=True)[:50]:
                run_id = re.sub(r"[^\w.-]", "", p.stem) or "trace"
                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
                events = _read_trace_events(p)
                summary = _summarize_events(events)
                result.append({
                    "run_id": run_id,
                    "timestamp": mtime,
                    "file": p.name,
                    "events": len(events),
                    **summary,
                })
        self._send_json(result)

    def _handle_trace_detail(self, run_id: str) -> None:
        safe_id = re.sub(r"[^\w.-]", "", run_id)
        traces_dir = self.server_root / ".castorcoder" / "traces"
        trace_file = traces_dir / f"{safe_id}.jsonl"
        self._send_json(_read_trace_events(trace_file))

    def _handle_memories(self) -> None:
        memories = []
        if self.memory_db and self.memory_db.is_file():
            try:
                conn = sqlite3.connect(str(self.memory_db))
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, kind, content, confidence, source, tags FROM memories "
                    "WHERE superseded_by IS NULL ORDER BY updated_at DESC LIMIT 100"
                )
                for row in cur.fetchall():
                    memories.append({
                        "id": row["id"],
                        "kind": row["kind"],
                        "content": row["content"],
                        "confidence": row["confidence"],
                        "source": row["source"],
                        "tags": row["tags"].split(",") if row["tags"] else [],
                    })
                conn.close()
            except Exception as exc:
                logger = logging.getLogger(__name__)
                logger.debug("Failed to read memories: %s", exc)
        self._send_json(memories)

    def _handle_gate(self) -> None:
        report_file = self.server_root / ".castorcoder" / "gate_report.json"
        if report_file.is_file():
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
                self._send_json(data)
                return
            except Exception:
                pass
        self._send_json({})


def start_dashboard(
    *,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    workspace_root: Path | str | None = None,
    server_root: Path | str | None = None,
    open_browser: bool = False,
) -> ThreadingHTTPServer:
    """Launch the dashboard HTTP server."""
    ws = Path(workspace_root or Path.cwd()).resolve()
    # Traces, sessions and the gate report all live under the workspace, so
    # the server root follows it unless the caller explicitly overrides it.
    s_root = Path(server_root or ws).resolve()
    db_candidate = ws / ".castorcoder" / "memory.db"
    if not db_candidate.is_file():
        db_candidate = s_root / ".castorcoder" / "memory.db"

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.workspace_root = ws
    BoundHandler.server_root = s_root
    BoundHandler.memory_db = db_candidate if db_candidate.is_file() else None

    server = ThreadingHTTPServer((host, port), BoundHandler)

    if open_browser:
        url = f"http://{host}:{server.server_address[1]}"
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    return server
