"""Tests for the zero-dependency CastorCoder developer cockpit / dashboard."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from castorcoder.dashboard.server import DashboardHandler


class DummySocket:
    """Mock socket for BaseHTTPRequestHandler testing without real network calls."""

    def __init__(self, request_bytes: bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def makefile(self, mode="r", buffering=-1):
        if "r" in mode:
            return self.rfile
        return self.wfile

    def sendall(self, data: bytes):
        self.wfile.write(data)


def simulate_get(handler_cls, path: str) -> tuple[int, dict[str, str], bytes]:
    raw_request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    sock = DummySocket(raw_request)
    handler_cls(sock, ("127.0.0.1", 12345), MagicMock())
    output = sock.wfile.getvalue()

    # Split headers and body
    header_part, _, body = output.partition(b"\r\n\r\n")
    lines = header_part.decode("utf-8", errors="ignore").split("\r\n")
    status_line = lines[0]
    status_code = int(status_line.split()[1])

    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    return status_code, headers, body


def test_dashboard_handler_endpoints(tmp_path: Path):
    castor_dir = tmp_path / ".castorcoder"
    castor_dir.mkdir(parents=True)

    # 1. Mock memory.db
    db_file = castor_dir / "memory.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE memories ("
        "id TEXT PRIMARY KEY, kind TEXT, content TEXT, confidence REAL, "
        "source TEXT, tags TEXT, created_at REAL, updated_at REAL, superseded_by TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("m1", "gotcha", "Watch out for Windows CRLF", 0.9, "run1", "win,crlf", 1.0, 1.0, None),
    )
    conn.commit()
    conn.close()

    # 2. Mock traces
    traces_dir = castor_dir / "traces"
    traces_dir.mkdir()
    trace_file = traces_dir / "run_abc.jsonl"
    trace_file.write_text(
        json.dumps({"type": "turn.started", "run_id": "run_abc", "data": {"index": 0}}) + "\n"
        + json.dumps({"type": "tool.completed", "run_id": "run_abc", "data": {"success": True}}) + "\n",
        encoding="utf-8",
    )

    # 3. Mock gate report
    gate_file = castor_dir / "gate_report.json"
    gate_file.write_text(
        json.dumps({"verdict": "SHIP", "tier1": {"passed": True}, "tier2": {}}),
        encoding="utf-8",
    )

    class TestHandler(DashboardHandler):
        server_root = tmp_path
        workspace_root = tmp_path
        memory_db = db_file

    # Test /
    code, hdrs, body = simulate_get(TestHandler, "/")
    assert code == 200
    assert "text/html" in hdrs.get("content-type", "")
    html = body.decode("utf-8")
    assert "CastorCoder Cockpit" in html
    assert "运行轨迹" in html and "长期记忆" in html and "发布门禁" in html

    # Test /api/status
    code, hdrs, body = simulate_get(TestHandler, "/api/status")
    assert code == 200
    data = json.loads(body.decode("utf-8"))
    assert data["memory_count"] == 1
    assert data["trace_count"] == 1
    assert data["workspace"] == str(tmp_path)

    # Test /api/memories
    code, hdrs, body = simulate_get(TestHandler, "/api/memories")
    assert code == 200
    mems = json.loads(body.decode("utf-8"))
    assert len(mems) == 1
    assert mems[0]["id"] == "m1"
    assert "Windows CRLF" in mems[0]["content"]

    # Test /api/traces
    code, hdrs, body = simulate_get(TestHandler, "/api/traces")
    assert code == 200
    traces = json.loads(body.decode("utf-8"))
    assert len(traces) == 1
    assert traces[0]["run_id"] == "run_abc"

    # Test /api/traces/<id>
    code, hdrs, body = simulate_get(TestHandler, "/api/traces/run_abc")
    assert code == 200
    events = json.loads(body.decode("utf-8"))
    assert len(events) == 2
    assert events[0]["type"] == "turn.started"

    # Test /api/sessions and /api/usage
    code, hdrs, body = simulate_get(TestHandler, "/api/sessions")
    assert code == 200
    assert isinstance(json.loads(body.decode("utf-8")), list)
    code, hdrs, body = simulate_get(TestHandler, "/api/usage")
    assert code == 200
    usage = json.loads(body.decode("utf-8"))
    assert "prompt_tokens" in usage and "cost_usd" in usage

    # Test /api/gate
    code, hdrs, body = simulate_get(TestHandler, "/api/gate")
    assert code == 200
    gate_data = json.loads(body.decode("utf-8"))
    assert gate_data["verdict"] == "SHIP"

    # Test 404
    code, _, body = simulate_get(TestHandler, "/api/unknown")
    assert code == 404
    assert "not found" in body.decode("utf-8")


def test_dashboard_trace_and_usage_summaries(tmp_path: Path, monkeypatch):
    castor_dir = tmp_path / ".castorcoder"
    traces_dir = castor_dir / "traces"
    traces_dir.mkdir(parents=True)
    events = [
        {"type": "run.started", "run_id": "run", "timestamp": 1_700_000_000.0,
         "data": {"model": "test-model"}},
        {"type": "llm.completed", "run_id": "run", "timestamp": 1_700_000_001.0,
         "data": {"prompt_tokens": 120, "completion_tokens": 30, "round": 1}},
        {"type": "tool.completed", "run_id": "run", "timestamp": 1_700_000_002.0,
         "data": {"name": "bash", "success": False, "duration_ms": 900}},
        {"type": "run.completed", "run_id": "run", "timestamp": 1_700_000_002.5,
         "data": {"stop_reason": "completed", "stats": {"rounds": 1}}},
    ]
    (traces_dir / "trace_run.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n{corrupt\n",
        encoding="utf-8",
    )

    ledger = tmp_path / "usage.jsonl"
    ledger.write_text(
        json.dumps({
            "ts": "2026-11-12T10:00:00",
            "model": "test-model",
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "rounds": 1,
            "tool_calls": 1,
            "tool_failures": 1,
            "cost_usd": 0.001,
            "stop_reason": "completed",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTORCODER_USAGE_LOG", str(ledger))

    class TestHandler(DashboardHandler):
        server_root = tmp_path
        workspace_root = tmp_path
        memory_db = None

    code, _, body = simulate_get(TestHandler, "/api/traces")
    assert code == 200
    traces = json.loads(body.decode("utf-8"))
    assert len(traces) == 1
    assert traces[0]["events"] == 4
    assert traces[0]["tokens_in"] == 120
    assert traces[0]["tokens_out"] == 30
    assert traces[0]["tool_calls"] == 1
    assert traces[0]["failures"] == 1
    assert traces[0]["stop_reason"] == "completed"
    assert traces[0]["started_at"] == 1_700_000_000.0
    assert traces[0]["duration_ms"] == 2500.0

    code, _, body = simulate_get(TestHandler, "/api/usage")
    usage = json.loads(body.decode("utf-8"))
    assert usage["recent"][0]["model"] == "test-model"
    assert usage["recent"][0]["tool_failures"] == 1

    code, _, body = simulate_get(TestHandler, "/api/status")
    status = json.loads(body.decode("utf-8"))
    assert status["model"] == "test-model"
