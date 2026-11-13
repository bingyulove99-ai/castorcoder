"""Tests for TaskWorkspace and AutoRunner feedback loop."""

from __future__ import annotations

import sys

from castorcoder.agent import Agent
from castorcoder.autorun import AutoRunner, AutoRunResult, TaskWorkspace
from castorcoder.llm import LLM, LLMResponse, ToolCall


def test_autorunner_discovery_and_execution(tmp_path):
    runner = AutoRunner(tmp_path)

    # Initially empty workspace -> no command
    assert runner.discover_command() is None
    assert runner.run() is None

    # Add verify.py
    verify_script = tmp_path / "verify.py"
    verify_script.write_text("import sys\nprint('verify check passed')\nsys.exit(0)\n", encoding="utf-8")
    assert runner.discover_command() == [sys.executable, "verify.py"]

    res = runner.run()
    assert res is not None
    assert res.passed
    assert res.exit_code == 0
    assert "verify check passed" in res.output
    assert "passed (exit code 0)" in res.format_feedback()

    # Failing script
    verify_script.write_text("import sys\nprint('assertion failure: 1 != 2')\nsys.exit(1)\n", encoding="utf-8")
    fail_res = runner.run()
    assert fail_res is not None
    assert not fail_res.passed
    assert fail_res.exit_code == 1
    assert "failed (exit code 1)" in fail_res.format_feedback()
    assert "assertion failure" in fail_res.format_feedback()


def test_task_workspace_manifest(tmp_path):
    ws = TaskWorkspace.create(
        base_dir=tmp_path,
        run_id="run_12345",
        task_prompt="Fix off-by-one error in binary search",
        model_name="claude-3-5-sonnet",
    )
    assert ws.root_dir.is_dir()

    auto_res = AutoRunResult(
        command=[sys.executable, "test_search.py"],
        exit_code=0,
        output="test_search: OK (2 passed)",
        elapsed_s=0.45,
    )

    manifest_path = ws.write_manifest(
        files_created=["search.py", "test_search.py"],
        auto_run=auto_res,
        duration_s=1.2,
    )
    assert manifest_path.is_file()
    text = manifest_path.read_text(encoding="utf-8")
    assert "# Run Manifest: run_12345" in text
    assert "Fix off-by-one error" in text
    assert "`search.py`" in text
    assert "`test_search.py`" in text
    assert "PASSED (exit 0)" in text
    assert "test_search: OK" in text


def test_agent_auto_verify_feedback_loop(tmp_path):
    # Setup test file in workspace
    (tmp_path / "verify.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    class MutatingScriptedLLM(LLM):
        def __init__(self):
            super().__init__(model="test", api_key="sk-fake")
            self.round = 0

        def chat(self, messages, tools=None, on_token=None):
            self.round += 1
            if self.round == 1:
                # Round 1: make a file edit that triggers auto-run
                return LLMResponse(
                    content="Writing new logic",
                    tool_calls=[
                        ToolCall(
                            id="call-write",
                            name="write_file",
                            arguments={"file_path": "foo.py", "content": "print('hello')\n"},
                        )
                    ],
                )
            # Round 2: finish
            return LLMResponse(content="All fixed!")

    agent = Agent(
        llm=MutatingScriptedLLM(),
        workspace=tmp_path,
        auto_verify=True,
    )

    result = agent.run("write foo.py")
    assert result.stats.rounds == 2
    # Verify feedback was injected into agent's message history
    feedback_msgs = [m for m in agent.messages if m.get("role") == "system" and "[AutoRun Feedback]" in str(m.get("content"))]
    assert len(feedback_msgs) >= 1
    assert "passed (exit code 0)" in feedback_msgs[0]["content"]
