"""CLI configuration, doctor, and smoke-path tests."""

from __future__ import annotations

import sys

from castorcoder import cli
from castorcoder.config import Config


def test_parse_args_supports_operational_flags():
    args = cli._parse_args([
        "--workspace", "repo", "--permission-mode", "plan", "--trace", "--max-cost", "1.5",
        "--max-rounds", "300", "--no-trace",
    ])
    assert args.workspace == "repo"
    assert args.permission_mode == "plan"
    assert args.trace is True
    assert args.no_trace is True
    assert args.max_cost == 1.5
    assert args.max_rounds == 300


def test_defaults_favor_long_runs_and_traces():
    config = Config()
    assert config.max_rounds == 200
    assert config.trace is True


def test_cli_overrides_cap_rounds_and_disable_traces(tmp_path):
    args = cli._parse_args(["--max-rounds", "900", "--no-trace"])
    config = Config()
    cli._apply_cli_overrides(config, args)
    assert config.max_rounds == 900
    assert config.trace is False

    assert cli.main(["--max-rounds", "0", "--doctor", "--workspace", str(tmp_path)]) == 2


def test_cli_effort_flag_overrides_config():
    args = cli._parse_args(["--effort", "max"])
    config = Config()
    cli._apply_cli_overrides(config, args)
    assert config.reasoning_effort == "max"


def test_main_applies_thinking_effort(tmp_path, monkeypatch):
    observed = {}

    def fake_run_once(agent, prompt):
        observed["effort"] = agent.llm.reasoning_effort

    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_run_once", fake_run_once)
    assert cli.main([
        "--workspace", str(tmp_path), "--effort", "max", "-p", "hello"
    ]) == 0
    assert observed["effort"] == "max"


def test_doctor_reports_tools_and_skills(tmp_path, monkeypatch, capsys):
    skill = tmp_path / ".castorcoder" / "skills" / "demo"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n")
    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    assert cli.main(["--doctor", "--workspace", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Tools: 16" in output
    assert "Skills: 1" in output
    assert "Code index: enabled" in output


def test_main_rejects_missing_openai_key(tmp_path, monkeypatch, capsys):
    for name in (
        "CASTORCODER_API_KEY", "CASTORCODER_API_KEY", "CASTORCODER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"
    ):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["--workspace", str(tmp_path)]) == 1
    assert "No API key" in capsys.readouterr().out


def test_main_one_shot_builds_agent_without_network(tmp_path, monkeypatch):
    observed = {}

    def fake_run_once(agent, prompt):
        observed["workspace"] = agent.workspace.root
        observed["prompt"] = prompt
        observed["index"] = agent.code_index is not None
        observed["memory"] = agent.memory_store is not None

    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_run_once", fake_run_once)
    result = cli.main(["--workspace", str(tmp_path), "-p", "hello"])
    assert result == 0
    assert observed == {
        "workspace": tmp_path.resolve(),
        "prompt": "hello",
        "index": True,
        "memory": True,
    }


def test_main_flags_disable_optional_stores(tmp_path, monkeypatch):
    observed = {}

    def fake_run_once(agent, prompt):
        observed["index"] = agent.code_index is not None
        observed["memory"] = agent.memory_store is not None

    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_run_once", fake_run_once)
    assert cli.main([
        "--workspace", str(tmp_path), "--no-index", "--no-memory", "-p", "hello"
    ]) == 0
    assert observed == {"index": False, "memory": False}


def test_main_reports_missing_resume_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    assert cli.main(["--workspace", str(tmp_path), "-r", "definitely-missing"]) == 1
    assert "not found" in capsys.readouterr().out


def test_main_reports_mcp_start_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp.servers.broken]\ncommand = "definitely-not-a-real-command"\n',
        encoding="utf-8",
    )
    result = cli.main([
        "--workspace", str(tmp_path), "--config", str(config), "--mcp", "-p", "hello"
    ])
    assert result == 2
    assert "MCP error" in capsys.readouterr().out


def test_doctor_without_key_returns_failure(tmp_path, monkeypatch, capsys):
    for name in (
        "CASTORCODER_API_KEY", "CASTORCODER_API_KEY", "CASTORCODER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"
    ):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["--doctor", "--workspace", str(tmp_path)]) == 1
    capsys.readouterr()


def test_approval_is_denied_when_not_interactive(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    approve = cli._make_approval()
    assert approve("write_file", {"file_path": "x"}, "policy") is False
    assert "non-interactive" in capsys.readouterr().out


def test_interactive_banner_shows_brand_mark(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    from castorcoder.agent import Agent
    from castorcoder.evals.runner import ScriptedLLM

    stream = io.StringIO()
    test_console = Console(file=stream, width=100, force_terminal=False, color_system=None)
    monkeypatch.setattr(cli, "console", test_console)

    agent = Agent(ScriptedLLM([]), workspace=tmp_path)
    cli._print_banner(agent, Config(workspace=str(tmp_path)), None)

    output = stream.getvalue()
    assert "CastorCoder" in output
    assert "|_||_|" in output and "(o)" in output
    assert "model" in output and "workspace" in output
    assert cli._INPUT_MESSAGE[0][1].startswith("You")


def test_repl_prints_the_banner_and_uses_the_framed_prompt(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    from castorcoder.agent import Agent
    from castorcoder.evals.runner import ScriptedLLM

    stream = io.StringIO()
    monkeypatch.setattr(
        cli,
        "console",
        Console(file=stream, width=100, force_terminal=False, color_system=None),
    )
    seen = {}

    class FakeSession:
        def prompt(self, message):
            seen["message"] = message
            return "quit"

    monkeypatch.setattr(cli, "_make_prompt_session", lambda *args, **kwargs: FakeSession())
    agent = Agent(ScriptedLLM([]), workspace=tmp_path)
    cli._repl(agent, Config(workspace=str(tmp_path)), None)

    output = stream.getvalue()
    assert "CastorCoder" in output and "|_||_|" in output
    assert seen["message"] is cli._INPUT_MESSAGE
    assert cli._INPUT_MESSAGE[0][1] == "You > "
