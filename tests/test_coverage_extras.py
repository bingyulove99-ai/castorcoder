"""Focused tests for edge paths in the new and optimized modules."""

from __future__ import annotations

import io
import json
import platform
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from castorcoder import tools as tools_package
from castorcoder.agent import Agent
from castorcoder.events import AgentEvent
from castorcoder.executor import ToolExecutor
from castorcoder.llm import LLMResponse, ToolCall
from castorcoder.metrics import TurnReceipt, UsageLedger
from castorcoder.planning import TaskBoard
from castorcoder.session import load_session_data, save_session
from castorcoder.tools import web
from castorcoder.tools.ask import AskUserTool
from castorcoder.tools.base import Tool, ToolRegistry
from castorcoder.tools.bash import BashTool
from castorcoder.tools.read import ReadFileTool
from castorcoder.tools.web import WebFetchTool, WebSearchTool
from castorcoder.ui import LiveRenderer
from castorcoder.workspace import Workspace


# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, url: str = "https://example.com/"):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._url = url

    def read(self, amount=None):
        return self._body if amount is None else self._body[:amount]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_html_json_text_and_error_paths(monkeypatch):
    monkeypatch.setattr(web, "_is_public_host", lambda host: True)
    responses = {
        "https://a.example": _FakeResponse(
            b"<html><head><title>T</title></head><body><p>Hello</p></body></html>",
            "text/html; charset=utf-8",
        ),
        "https://b.example": _FakeResponse(b'{"ok": true}', "application/json"),
        "https://c.example": _FakeResponse(b"plain body", "text/plain"),
    }
    monkeypatch.setattr(web.urllib.request, "urlopen",
                        lambda request, timeout: responses[request.full_url])

    html = WebFetchTool().execute("https://a.example", max_chars=500)
    assert "Title: T" in html and "Hello" in html
    structured = WebFetchTool().execute("https://b.example")
    assert '"ok": true' in structured
    plain = WebFetchTool().execute("https://c.example")
    assert "plain body" in plain

    def boom(request, timeout):
        raise OSError("offline")
    monkeypatch.setattr(web.urllib.request, "urlopen", boom)
    assert "could not fetch" in WebFetchTool().execute("https://a.example")


def test_safe_url_rejects_missing_host_and_bad_input(monkeypatch):
    assert "no host" in web._safe_url("http://")
    assert "only http" in web._safe_url("ftp://example.com/x")
    assert "credentials" in web._safe_url("https://u:p@example.com/")
    monkeypatch.setattr(web, "_is_public_host", lambda host: True)
    target, parsed = web._safe_url("https://example.com/path")
    assert target == "https://example.com/path" and parsed.hostname == "example.com"


def test_tavily_and_duckduckgo_parsers(monkeypatch):
    payload = json.dumps({"results": [{"title": "A", "content": "B", "url": "https://x"}]}).encode("utf-8")
    monkeypatch.setattr(web.urllib.request, "urlopen",
                        lambda request, timeout: _FakeResponse(payload, "application/json"))
    assert web._tavily("q", "key", 3) == [("A", "B", "https://x")]

    page = (
        b'<a class="result__a" href="https://duck/?uddg=https%3A%2F%2Freal.example">'
        b"Title &amp; More</a>"
        b'<a class="result__snippet">Snippet <b>text</b></a>'
    )
    monkeypatch.setattr(web.urllib.request, "urlopen",
                        lambda request, timeout: _FakeResponse(page, "text/html"))
    results = web._duckduckgo("q", 3)
    assert results[0][0] == "Title & More"
    assert results[0][2] == "https://real.example"


def test_search_uses_tavily_when_key_is_present(monkeypatch):
    monkeypatch.setenv("CASTORCODER_SEARCH_API_KEY", "key")
    monkeypatch.setattr(web, "_tavily", lambda query, key, n: [("T", "S", "U")])
    output = WebSearchTool().execute("q", max_results=2)
    assert "via Tavily" in output and "T" in output


def test_search_reports_backend_exception_with_key(monkeypatch):
    monkeypatch.setenv("CASTORCODER_SEARCH_API_KEY", "key")

    def boom(query, key, n):
        raise OSError("down")
    monkeypatch.setattr(web, "_tavily", boom)
    assert "Web search failed" in WebSearchTool().execute("q")


# ---------------------------------------------------------------------------
# Read / glob / grep edge paths
# ---------------------------------------------------------------------------
def test_read_binary_large_and_directory_listing(tmp_path):
    tool = ReadFileTool(Workspace(tmp_path))
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    assert "binary" in tool.execute("blob.bin")

    large = tmp_path / "large.txt"
    with large.open("wb") as handle:
        handle.truncate(9_000_000)
    assert "too large" in tool.execute("large.txt")

    (tmp_path / "note.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "data.json").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x00")
    (tmp_path / "sub").mkdir()
    listing = tool.execute(".")
    assert "[DIR]  sub/" in listing
    assert "note.py" in listing and "lines" in listing
    assert "data.json" in listing
    assert "image.png" in listing

    (tmp_path / "unique_target.py").write_text("pass\n", encoding="utf-8")
    suggestion = tool.execute("unique_target")
    assert "Did you mean" in suggestion

    many = tmp_path / "many.txt"
    many.write_text("\n".join(f"line-{i}" for i in range(20)), encoding="utf-8")
    assert "showing" in tool.execute("many.txt", offset=10, limit=1)
    assert "(empty file)" in tool.execute("many.txt", offset=100, limit=1)


def test_glob_no_match_and_file_error(tmp_path):
    from castorcoder.tools.glob_tool import GlobTool

    tool = GlobTool(Workspace(tmp_path))
    assert "No files matched" in tool.execute("*.missing")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    assert "not a directory" in tool.execute("**/*.txt", path="a.txt")
    assert "a.txt" in tool.execute("**/*.txt")
    assert "a.txt" in tool.execute("*.txt")


def test_grep_edge_paths(tmp_path):
    from castorcoder.tools.grep import GrepTool

    tool = GrepTool(Workspace(tmp_path))
    assert "Invalid regex" in tool.execute("[")
    assert "not found" in tool.execute("x", path="missing")
    (tmp_path / "a.py").write_text("Alpha\nbeta\nalpha\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("alpha\n", encoding="utf-8")

    assert "No matches" in tool.execute("ALPHA", case_sensitive=True)
    assert "a.py" in tool.execute("ALPHA", case_sensitive=False)
    assert "b.txt" not in tool.execute("ALPHA", case_sensitive=False, include="*.py")
    assert "match limit" in tool.execute("ALPHA", case_sensitive=False, max_results=1)
    assert "Alpha" in tool.execute("Alpha", context=1)

    # A file path skips the directory walk entirely.
    assert "a.py" in tool.execute("Alpha", path="a.py")


def test_grep_ripgrep_fast_path(monkeypatch, tmp_path):
    from castorcoder.tools import grep as grep_module

    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    tool = grep_module.GrepTool(Workspace(tmp_path))
    monkeypatch.setattr(grep_module.shutil, "which", lambda name: "/usr/bin/rg")

    class Proc:
        stdout = "a.py:1: x\n"
        returncode = 0

    monkeypatch.setattr(grep_module.subprocess, "run", lambda *a, **k: Proc())
    assert "a.py" in tool.execute("x")


# ---------------------------------------------------------------------------
# Ask user / UI events
# ---------------------------------------------------------------------------
def test_ask_user_callback_paths():
    assert "no interactive user" in AskUserTool().execute("which one?")
    assert "empty" in AskUserTool().execute("  ")
    assert "Ada" in AskUserTool(lambda question, options: "Ada").execute("name?")
    assert "no answer" in AskUserTool(lambda question, options: "").execute("name?")

    def boom(question, options):
        raise RuntimeError("no tty")
    assert "could not ask" in AskUserTool(boom).execute("name?")


def _ui_renderer(**kwargs):
    stream = io.StringIO()
    console = Console(file=stream, width=120, force_terminal=False, color_system=None)
    return LiveRenderer(console, status=False, **kwargs), stream


def test_ui_handles_all_event_kinds_and_receipt():
    renderer, stream = _ui_renderer(reasoning="off")
    renderer.begin()
    renderer.on_event(AgentEvent("run.started", "r", data={"model": "m"}))
    renderer.on_event(AgentEvent("llm.started", "r", data={"round": 2}))
    renderer.on_event(AgentEvent("llm.completed", "r", data={
        "prompt_tokens": 10, "completion_tokens": 2, "reasoning_chars": 5,
    }))
    renderer.on_event(AgentEvent("context.trimmed", "r", data={"count": 2}))
    renderer.on_event(AgentEvent("context.compressed", "r", data={"tokens_estimate": 100}))
    renderer.on_event(AgentEvent("memory.retrieved", "r", data={"count": 3}))
    renderer.on_event(AgentEvent("memory.written", "r", data={"count": 1}))
    renderer.on_event(AgentEvent("autorun.completed", "r", data={
        "passed": False, "command": "pytest",
    }))
    renderer.on_event(AgentEvent("tool.completed", "r", data={
        "name": "bash", "success": False, "duration_ms": 4, "cached": True,
    }))
    stats = SimpleNamespace(
        run_id="r", session_id="s", model="m", prompt_tokens=10,
        completion_tokens=2, cost_usd=None, rounds=1, tool_calls=1,
        tool_failures=1, cache_reuses=0, trimmed_results=0, elapsed_ms=100,
        stop_reason="tool_errors",
    )
    renderer.finish(SimpleNamespace(stats=stats))
    output = stream.getvalue()
    assert "trimmed 2" in output
    assert "memory" in output
    assert "auto-verify" in output
    assert "Stopped: tool_errors" in output
    assert "cost n/a" in output


def test_ui_status_mode_and_helpers():
    stream = io.StringIO()
    console = Console(file=stream, width=100, force_terminal=True, color_system=None)
    renderer = LiveRenderer(console, reasoning="brief", reasoning_chars=5, status=True)
    renderer.begin()
    time.sleep(0.12)
    renderer.on_reasoning("abcdef")
    renderer.pause_for_input()
    renderer.resume_after_input()
    renderer.note("note")
    renderer.print("printed")
    renderer.finish()
    assert "Thinking" in stream.getvalue()


def test_ui_argument_briefing():
    from castorcoder.ui import _brief_argument

    assert _brief_argument(None) == ""
    assert "x=" in _brief_argument({"x": "y"})
    long = _brief_argument({"x": "z" * 100}, limit=20)
    assert long.endswith("...")


# ---------------------------------------------------------------------------
# Executor ordering, cancellation, receipts
# ---------------------------------------------------------------------------
class ProbeTool(Tool):
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def __init__(self, name, risk="read", safe=True):
        self.name = name
        self.description = name
        self.risk = risk
        self.parallel_safe = safe
        self.calls = 0
        self.lock = threading.Lock()

    def execute(self, value: str) -> str:
        with self.lock:
            self.calls += 1
        return f"{self.name}:{value}"


def test_executor_groups_safe_runs_and_keeps_order():
    first = ProbeTool("first")
    second = ProbeTool("second")
    write = ProbeTool("write", risk="write", safe=False)
    third = ProbeTool("third")
    executor = ToolExecutor(ToolRegistry([first, second, write, third]))
    calls = [
        ToolCall("1", "first", {"value": "a"}),
        ToolCall("2", "second", {"value": "b"}),
        ToolCall("3", "write", {"value": "c"}),
        ToolCall("4", "third", {"value": "d"}),
    ]
    results = executor.execute_many(calls, "run")
    assert [item.output for item in results] == ["first:a", "second:b", "write:c", "third:d"]


def test_executor_cancellation_and_unknown_tool_suggestion():
    executor = ToolExecutor(ToolRegistry([ProbeTool("search_code")]))
    cancelled = threading.Event()
    cancelled.set()
    result = executor.execute(ToolCall("1", "search_code", {"value": "x"}), "r", cancelled)
    assert "cancelled" in result.output

    unknown = executor.execute(ToolCall("2", "search_cod", {"value": "x"}), "r")
    assert "Did you mean: search_code" in unknown.output


def test_receipt_and_ledger_edge_cases(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text("not json\n[]\n", encoding="utf-8")
    ledger = UsageLedger(path)
    summary = ledger.summary()
    assert summary["runs"] == 0  # malformed JSON and non-object rows are skipped
    receipt = TurnReceipt(prompt_tokens=1, completion_tokens=2)
    assert "cost n/a" in receipt.line()
    missing = UsageLedger(tmp_path / "missing.jsonl").summary()
    assert missing["runs"] == 0


# ---------------------------------------------------------------------------
# Session / planning / tool factory
# ---------------------------------------------------------------------------
def test_planning_load_restores_valid_items():
    board = TaskBoard()
    board.load([
        {"id": 3, "title": "a", "status": "completed", "note": "n"},
        {"id": 0, "title": "b", "status": "bogus"},
        {"id": 9, "title": "", "status": "pending"},
        "not a dict",
    ])
    snapshot = board.snapshot()
    assert len(snapshot) == 2
    assert snapshot[0]["id"] == 3
    assert snapshot[1]["status"] == "pending"
    board.add("c")
    assert board.snapshot()[-1]["id"] == 5


def test_session_helpers_handle_bad_pointer_and_missing_meta(tmp_path, monkeypatch):
    from castorcoder import session as session_module

    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir(parents=True)
    (tmp_path / "sessions" / "last_session.json").write_text("{broken", encoding="utf-8")
    assert session_module.latest_session(tmp_path) is None

    sid = save_session([{"role": "user", "content": "x"}], "m", "legacy")
    assert load_session_data(sid)["metadata"] == {}


def test_index_and_grep_skip_credential_files(tmp_path):
    from castorcoder.index import CodeIndex
    from castorcoder.tools.grep import GrepTool

    (tmp_path / ".env").write_text("API_KEY=super-secret\n", encoding="utf-8")
    (tmp_path / "private.pem").write_text("super-secret\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    index = CodeIndex(tmp_path, db_path=tmp_path / "db" / "index.db", auto_build=False)
    stats = index.update()
    assert stats.files_indexed == 1
    hits = index.search("super-secret")
    assert all("super-secret" not in hit.chunk.content for hit in hits)
    assert all(hit.chunk.path not in {".env", "private.pem"} for hit in hits)

    grep = GrepTool(Workspace(tmp_path))
    assert "No matches" in grep.execute("super-secret")
    assert "super-secret" in grep.execute("super-secret", path=".env")


def test_tool_factory_lookup():
    tool = tools_package.get_tool("bash", [BashTool(Workspace(Path.cwd()))])
    assert tool is not None and tool.name == "bash"
    assert tools_package.get_tool("definitely-missing") is None


# ---------------------------------------------------------------------------
# Agent failure/trim paths
# ---------------------------------------------------------------------------
class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.model = "scripted"
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def estimated_cost(self):
        return None

    def chat(self, messages, tools=None, on_token=None):
        return self.responses.pop(0)


class FailTool(ProbeTool):
    def __init__(self):
        super().__init__("fail", risk="write", safe=False)

    def execute(self, value: str) -> str:
        self.calls += 1
        return "Error: still broken"


def test_agent_stops_after_consecutive_all_failed_rounds(tmp_path):
    responses = [
        LLMResponse(tool_calls=[ToolCall(f"c{i}", "fail", {"value": f"{i}"})])
        for i in range(3)
    ] + [LLMResponse(content="unreachable")]
    agent = Agent(
        ScriptedLLM(responses),
        tools=[FailTool()],
        workspace=tmp_path,
        max_rounds=10,
        max_consecutive_failures=3,
    )
    result = agent.run("try")
    assert result.stats.stop_reason == "tool_errors"


def test_agent_final_answer_falls_back_when_model_fails(tmp_path):
    responses = [LLMResponse(tool_calls=[ToolCall("c1", "echo", {"value": "x"})])]

    class Echo(ProbeTool):
        def __init__(self):
            super().__init__("echo")

        def execute(self, value: str) -> str:
            return value

    agent = Agent(ScriptedLLM(responses), tools=[Echo()], workspace=tmp_path, max_rounds=1)
    result = agent.run("x")
    assert result.stats.stop_reason == "max_rounds"
    assert "maximum tool-call rounds" in result.output


def test_agent_trims_old_results_between_rounds(tmp_path):
    class BigRead(ProbeTool):
        def __init__(self):
            super().__init__("big_read")

        def execute(self, value: str) -> str:
            return "x" * 5_000

    responses = [
        LLMResponse(tool_calls=[ToolCall("c1", "big_read", {"value": "a"})]),
        LLMResponse(tool_calls=[ToolCall("c2", "big_read", {"value": "b"})]),
        LLMResponse(content="done"),
    ]
    agent = Agent(ScriptedLLM(responses), tools=[BigRead()], workspace=tmp_path,
                  trim_long_chars=1_000)
    result = agent.run("read")
    assert result.stats.trimmed_results == 1


def test_agent_reports_loop_guard_on_repeated_failed_signature(tmp_path):
    responses = [LLMResponse(tool_calls=[ToolCall("c1", "fail", {"value": "same"})]) for _ in range(2)]
    responses.append(LLMResponse(content="give up"))
    agent = Agent(ScriptedLLM(responses), tools=[FailTool()], workspace=tmp_path, max_rounds=5)
    result = agent.run("edit")
    assert result.output == "give up"
    assert agent.last_receipt is not None


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows shell selection")
def test_bash_shell_selection_branches(monkeypatch, tmp_path):
    tool = BashTool(Workspace(tmp_path))
    assert "hello" in tool.execute("echo hello")

    monkeypatch.setattr("castorcoder.tools.bash._bash_usable", lambda: False)
    argv, shell, encoding = tool._shell_for("ls -la")
    assert argv and argv[-1] == "dir /a" and shell is False
    argv, shell, _ = tool._shell_for("Get-ChildItem")
    assert argv and "powershell" in argv[0]
    argv, shell, _ = tool._shell_for("echo hi")
    assert argv is None and shell is True
