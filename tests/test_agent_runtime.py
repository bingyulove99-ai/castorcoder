"""Agent loop, executor policy, concurrency, events, and loop-guard tests."""

from __future__ import annotations

import json
import threading
import time

from castorcoder.agent import Agent
from castorcoder.executor import PermissionPolicy, ToolExecutor, redact_data, redact_secrets
from castorcoder.llm import LLMResponse, ToolCall
from castorcoder.tools.agent import AgentTool
from castorcoder.tools.base import Tool, ToolRegistry


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
        response = self.responses.pop(0)
        if response.content and on_token:
            on_token(response.content)
        self.total_prompt_tokens += response.prompt_tokens
        self.total_completion_tokens += response.completion_tokens
        return response


class EchoTool(Tool):
    name = "echo"
    description = "Echo a value."
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def execute(self, value: str) -> str:
        return f"echo:{value}"


class WriteRiskTool(EchoTool):
    name = "write_risk"
    risk = "write"
    parallel_safe = False


def _tool_response(call_id="call-1", name="echo", value="hello"):
    return LLMResponse(tool_calls=[ToolCall(call_id, name, {"value": value})])


def test_custom_tool_is_executed_from_instance_registry(tmp_path):
    llm = ScriptedLLM([_tool_response(), LLMResponse(content="done")])
    agent = Agent(llm, tools=[EchoTool()], workspace=tmp_path)
    result = agent.run("do it")
    assert result.output == "done"
    assert result.stats.tool_calls == 1
    assert any(message.get("content") == "echo:hello" for message in agent.messages)


def test_plan_mode_blocks_mutating_tool(tmp_path):
    llm = ScriptedLLM([
        _tool_response(name="write_risk"),
        LLMResponse(content="could not write"),
    ])
    agent = Agent(llm, tools=[WriteRiskTool()], workspace=tmp_path, permission_mode="plan")
    result = agent.run("plan only")
    assert result.stats.tool_failures == 1
    assert any(str(message.get("content", "")).startswith("Blocked:") for message in agent.messages)


def test_full_access_constructs_unbounded_workspace(tmp_path):
    agent = Agent(
        ScriptedLLM([LLMResponse(content="ok")]),
        tools=[EchoTool()],
        workspace=tmp_path,
        permission_mode="full_access",
    )
    assert agent.workspace.allow_outside is True


def test_repeated_tool_batch_triggers_loop_guard(tmp_path):
    responses = [_tool_response(call_id=f"call-{index}") for index in range(3)]
    agent = Agent(ScriptedLLM(responses), tools=[EchoTool()], workspace=tmp_path)
    result = agent.run("loop")
    assert result.stats.stop_reason == "loop_guard"
    assert result.stats.tool_calls == 2


def test_trace_contains_structured_lifecycle_events(tmp_path):
    trace = tmp_path / "trace.jsonl"
    agent = Agent(
        ScriptedLLM([LLMResponse(content="ok", prompt_tokens=4, completion_tokens=1)]),
        tools=[EchoTool()],
        workspace=tmp_path,
        trace_path=trace,
    )
    agent.run("hello")
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in events] == [
        "run.started", "llm.started", "llm.completed", "run.completed"
    ]
    assert len({event["run_id"] for event in events}) == 1


class ConcurrencyProbe(EchoTool):
    def __init__(self, parallel_safe=True):
        self.parallel_safe = parallel_safe
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()

    def execute(self, value: str) -> str:
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return value


def test_executor_parallelises_only_safe_batches():
    safe = ConcurrencyProbe(parallel_safe=True)
    executor = ToolExecutor(ToolRegistry([safe]), permission_policy=PermissionPolicy("workspace"))
    calls = [ToolCall(str(index), "echo", {"value": str(index)}) for index in range(3)]
    executor.execute_many(calls, "run-safe")
    assert safe.maximum > 1

    unsafe = ConcurrencyProbe(parallel_safe=False)
    executor = ToolExecutor(ToolRegistry([unsafe]))
    executor.execute_many(calls, "run-unsafe")
    assert unsafe.maximum == 1


def test_secret_redaction():
    text = redact_secrets(
        '"api_key": "abc123" password: hunter2 Bearer abcdefghijk sk-abcdefghijklmnopqrstuvwxyz'
    )
    assert "abc123" not in text and "hunter2" not in text
    assert text.count("[REDACTED]") == 4
    assert redact_data({"headers": {"Authorization": "Bearer secret-value"}}) == {
        "headers": {"Authorization": "[REDACTED]"}
    }


def test_executor_reports_tool_argument_json_error():
    executor = ToolExecutor(ToolRegistry([EchoTool()]))
    call = ToolCall("bad", "echo", {}, arguments_error="unterminated object")
    result = executor.execute(call, "run-bad-json")
    assert not result.success
    assert "invalid JSON" in result.output


class CountingReadTool(EchoTool):
    name = "counting_read"
    risk = "read"

    def __init__(self):
        self.calls = 0

    def execute(self, value: str) -> str:
        self.calls += 1
        return f"read:{value}"


def test_identical_read_only_call_is_reused_within_a_run(tmp_path):
    tool = CountingReadTool()
    responses = [
        _tool_response("call-1", "counting_read", "same"),
        _tool_response("call-2", "counting_read", "same"),
        LLMResponse(content="done"),
    ]
    agent = Agent(ScriptedLLM(responses), tools=[tool], workspace=tmp_path)
    result = agent.run("inspect")

    assert result.output == "done"
    assert tool.calls == 1
    assert result.stats.cache_reuses == 1


def test_max_rounds_asks_for_a_final_answer_without_tools(tmp_path):
    responses = [
        _tool_response("call-1", "echo", "hello"),
        LLMResponse(content="answered from evidence", prompt_tokens=5, completion_tokens=2),
    ]
    agent = Agent(
        ScriptedLLM(responses), tools=[EchoTool()], workspace=tmp_path, max_rounds=1
    )
    result = agent.run("do it")

    assert result.stats.stop_reason == "max_rounds"
    assert result.output == "answered from evidence"
    assert result.stats.prompt_tokens == 5
    assert result.stats.completion_tokens == 2


class FailingWriteTool(EchoTool):
    name = "failing_write"
    risk = "write"
    parallel_safe = False

    def __init__(self):
        self.calls = 0

    def execute(self, value: str) -> str:
        self.calls += 1
        return "Error: old_string not found"


def test_repeated_failures_are_blocked_even_when_interleaved(tmp_path):
    failing = FailingWriteTool()
    failing_call = "failing_write"
    responses = [
        _tool_response("1", failing_call, "same"),
        _tool_response("2", "counting_read", "other"),
        _tool_response("3", failing_call, "same"),
        _tool_response("4", "counting_read", "other"),
        _tool_response("5", failing_call, "same"),
        LLMResponse(content="changed approach"),
    ]
    agent = Agent(
        ScriptedLLM(responses),
        tools=[failing, CountingReadTool()],
        workspace=tmp_path,
        max_rounds=8,
    )
    result = agent.run("edit")

    assert result.output == "changed approach"
    # The third identical failing call is refused before it reaches the tool.
    assert failing.calls == 2
    assert any(
        "has already failed or repeated" in str(message.get("content", ""))
        for message in agent.messages
    )


def test_turn_receipt_and_usage_ledger(tmp_path):
    from castorcoder.metrics import UsageLedger

    ledger = UsageLedger(tmp_path / "usage.jsonl")
    agent = Agent(
        ScriptedLLM([LLMResponse(content="ok", prompt_tokens=10, completion_tokens=3)]),
        tools=[EchoTool()],
        workspace=tmp_path,
        usage_ledger=ledger,
        session_id="session-test",
    )
    result = agent.run("hi")

    assert result.stats.session_id == "session-test"
    assert result.stats.prompt_tokens == 10
    receipt = agent.last_receipt
    assert receipt is not None and receipt.session_id == "session-test"
    assert receipt.as_dict()["prompt_tokens"] == 10
    assert (tmp_path / "usage.jsonl").is_file()
    assert ledger.summary()["runs"] == 1


def test_subagent_runs_with_isolated_context(tmp_path):
    parent = Agent(
        ScriptedLLM([LLMResponse(content="research result")]),
        workspace=tmp_path,
    )
    agent_tool = next(tool for tool in parent.tools if isinstance(tool, AgentTool))
    output = agent_tool.execute("inspect the repository", role="reviewer", mode="research")
    assert "Sub-agent completed: reviewer/research" in output
    assert "research result" in output
    assert parent.messages == []


class EmptyReplyLLM:
    """Model double that can return empty/truncated completions on demand."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[int | None] = []
        self.model = "empty-test"
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.max_output_tokens = 4_096

    @property
    def estimated_cost(self):
        return None

    def chat(self, messages, tools=None, on_token=None, max_tokens=None):
        self.calls.append(max_tokens)
        return self.replies.pop(0)


def test_empty_truncated_reply_is_retried_with_larger_budget(tmp_path):
    llm = EmptyReplyLLM([
        LLMResponse(content="", finish_reason="length", completion_tokens=4_096),
        LLMResponse(content="recovered", finish_reason="stop", completion_tokens=12),
    ])
    agent = Agent(llm, workspace=tmp_path)
    result = agent.run("write it")

    assert result.output == "recovered"
    assert result.stats.stop_reason == "completed"
    # The retry doubles the configured cap; the empty reply is not kept in
    # history, and the harness nudge explains what went wrong.
    assert llm.calls == [None, 8_192]
    assert not any(
        message.get("role") == "assistant" and message.get("content") is None
        for message in agent.messages
    )
    assert any(
        "cut off by the output token limit" in str(message.get("content", ""))
        for message in agent.messages
    )


def test_empty_reply_stops_with_visible_explanation(tmp_path):
    llm = EmptyReplyLLM([LLMResponse(content=None, finish_reason="stop") for _ in range(3)])
    agent = Agent(llm, workspace=tmp_path)
    result = agent.run("write it")

    assert result.stats.stop_reason == "empty_response"
    assert "returned no content" in result.output
    assert agent.messages[-1]["role"] == "assistant"
    assert agent.messages[-1]["content"] == result.output
