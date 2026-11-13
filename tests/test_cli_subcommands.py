"""Tests for new CLI subcommands: dashboard, gate, and graph."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from castorcoder import cli
from castorcoder.agent import Agent
from castorcoder.config import Config
from castorcoder.evals.runner import ScriptedLLM


def test_parse_args_subcommands():
    # Dashboard args
    args_dash = cli._parse_args(["dashboard", "--port", "9000", "--host", "0.0.0.0", "--open"])
    assert args_dash.subcommand == "dashboard"
    assert args_dash.port == 9000
    assert args_dash.host == "0.0.0.0"
    assert args_dash.open is True

    # Gate args
    args_gate = cli._parse_args(["gate", "--skip-tier2", "--report", "custom_report.json"])
    assert args_gate.subcommand == "gate"
    assert args_gate.skip_tier2 is True
    assert args_gate.report == "custom_report.json"

    # Graph args
    args_graph = cli._parse_args(["graph", "fix", "-p", "Fix issue #42", "-m", "mock-model"])
    assert args_graph.subcommand == "graph"
    assert args_graph.workflow == "fix"
    assert args_graph.prompt == "Fix issue #42"
    assert args_graph.model == "mock-model"


def test_cli_dashboard_subcommand(monkeypatch, capsys):
    mock_server = MagicMock()
    mock_server.serve_forever.side_effect = KeyboardInterrupt

    with patch("castorcoder.dashboard.server.start_dashboard", return_value=mock_server) as mock_start:
        ret = cli.main(["dashboard", "--port", "8080", "--host", "127.0.0.1"])
        assert ret == 0
        mock_start.assert_called_once()
        mock_server.serve_forever.assert_called_once()
        mock_server.server_close.assert_called_once()


def test_cli_gate_subcommand(tmp_path: Path):
    mock_verdict_pass = MagicMock(passed=True)
    mock_verdict_pass.as_dict.return_value = {"passed": True}
    report = tmp_path / "custom_report.json"
    with patch("castorcoder.evals.gate.ReleaseGate.evaluate", return_value=mock_verdict_pass) as evaluate:
        ret = cli.main(
            ["gate", "-w", str(tmp_path), "--skip-tier2", "--report", str(report)]
        )
        assert ret == 0
        evaluate.assert_called_once_with(save_report=True, skip_tier2=True)
        assert report.is_file()

    mock_verdict_fail = MagicMock(passed=False)
    with patch("castorcoder.evals.gate.ReleaseGate.evaluate", return_value=mock_verdict_fail):
        ret = cli.main(["gate", "-w", str(tmp_path)])
        assert ret == 1


def test_workspace_changed_files_handles_non_git(tmp_path: Path, monkeypatch):
    # tmp_path lives inside this repository under .tmp/pytest; stop git from
    # walking up so the test really exercises a non-git workspace.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert cli._workspace_changed_files(tmp_path) == []


def test_cli_graph_subcommand(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_API_KEY", "test-key")

    mock_wf = MagicMock()
    mock_wf.run.return_value = {"verified": True, "final_report": "Pipeline completed"}

    with patch("castorcoder.graph.workflows.create_code_fix_workflow", return_value=mock_wf) as factory:
        ret = cli.main(["graph", "fix", "-p", "Repair broken test", "-w", str(tmp_path)])
        assert ret == 0
        mock_wf.run.assert_called_once_with({"task": "Repair broken test"})
        assert isinstance(factory.call_args.args[0], Agent)


def test_repl_gate_command(tmp_path: Path, capsys):
    agent = Agent(ScriptedLLM([]), workspace=tmp_path)
    config = Config(workspace=str(tmp_path))

    mock_verdict = MagicMock(passed=True)
    with patch("castorcoder.evals.gate.ReleaseGate.evaluate", return_value=mock_verdict):
        res = cli._handle_command("/gate", agent, config, None)
        assert res is True
        out = capsys.readouterr().out
        assert "SHIP" in out
