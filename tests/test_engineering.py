"""Atomic writes, tool timeouts, provider pricing, MCP config and approvals."""

from __future__ import annotations

import time

from castorcoder.config import Config
from castorcoder.executor import PermissionPolicy, ToolExecutor
from castorcoder.fsutil import atomic_write_text
from castorcoder.llm import LLM, ToolCall
from castorcoder.tools.base import Tool, ToolRegistry


class SleepTool(Tool):
    name = "sleep"
    description = "Sleep for a while."
    parameters = {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
        "additionalProperties": False,
    }
    timeout_s = 0.05

    def execute(self, seconds: float) -> str:
        time.sleep(seconds)
        return "done"


class WriteRiskTool(Tool):
    name = "write_risk"
    description = "Pretend to write."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    risk = "write"
    parallel_safe = False

    def execute(self) -> str:
        return "written"


def test_atomic_write_text_leaves_no_temp_files(tmp_path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert [path.name for path in target.parent.iterdir()] == ["file.txt"]


def test_executor_enforces_tool_timeout():
    executor = ToolExecutor(ToolRegistry([SleepTool()]))
    started = time.perf_counter()
    result = executor.execute(ToolCall("1", "sleep", {"seconds": 0.5}), "run")
    elapsed = time.perf_counter() - started
    assert not result.success
    assert "timed out" in result.output
    assert elapsed < 0.4


def test_approval_callback_overrides_plan_policy():
    seen = []

    def approve(name, arguments, reason):
        seen.append((name, reason))
        return True

    executor = ToolExecutor(
        ToolRegistry([WriteRiskTool()]),
        permission_policy=PermissionPolicy("plan"),
        approval_callback=approve,
    )
    result = executor.execute(ToolCall("1", "write_risk", {}), "run")
    assert result.success
    assert result.output == "written"
    assert seen and "blocks write tools" in seen[0][1]


def test_provider_prefixed_pricing():
    llm = LLM.__new__(LLM)
    llm.total_prompt_tokens = 1_000_000
    llm.total_completion_tokens = 500_000
    llm.model = "anthropic/claude-sonnet-4-6"
    assert llm.estimated_cost == 10.5
    llm.model = "unknown/unknown-model"
    assert llm.estimated_cost is None


def test_config_parses_and_redacts_mcp_servers(tmp_path, monkeypatch):
    for name in ("CASTORCODER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.toml"
    path.write_text(
        '[castorcoder]\nmodel = "local-model"\n\n'
        '[mcp.servers.fake]\ncommand = "python"\nargs = ["server.py"]\n'
        'env = { TOKEN = "secret" }\n',
        encoding="utf-8",
    )
    config = Config.from_env(path)
    assert config.model == "local-model"
    assert "fake" in config.mcp_servers
    public = config.public_dict()
    assert public["mcp_servers"]["fake"]["env"] == "[REDACTED]"
    assert "secret" not in str(public)


def test_config_rejects_mcp_without_command(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[mcp.servers.broken]\nargs = []\n", encoding="utf-8")
    try:
        Config.from_env(path)
    except ValueError as exc:
        assert "command" in str(exc)
    else:
        raise AssertionError("invalid MCP configuration was accepted")
