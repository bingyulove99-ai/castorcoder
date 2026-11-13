"""Tests for the constructed-argv Git inspection tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from castorcoder.tools.git_tool import GitTool
from castorcoder.workspace import Workspace


def test_git_tool_metadata():
    ws = MagicMock(spec=Workspace)
    tool = GitTool(ws)
    assert tool.name == "git"
    assert tool.risk == "read"
    assert tool.parallel_safe is True
    schema = tool.schema()
    assert schema["function"]["name"] == "git"
    assert "subcommand" in schema["function"]["parameters"]["properties"]


def test_git_tool_blocks_unsafe_subcommands():
    ws = MagicMock(spec=Workspace)
    tool = GitTool(ws)

    res = tool.execute(subcommand="reset", args=["--hard"])
    assert "Blocked: subcommand 'reset' is not permitted" in res

    res = tool.execute(subcommand="push")
    assert "Blocked: subcommand 'push' is not permitted" in res


def test_git_tool_blocks_forbidden_flags_and_control_chars():
    ws = MagicMock(spec=Workspace)
    tool = GitTool(ws)

    res = tool.execute(subcommand="log", args=["--config", "core.fileMode=false"])
    assert "Blocked: flag '--config' is forbidden" in res

    res = tool.execute(subcommand="log", args=["--upload-pack=exploit"])
    assert "Blocked: flag '--upload-pack=exploit' is forbidden" in res

    res = tool.execute(subcommand="status", args=["clean\nrm -rf /"])
    assert "Blocked: argument contains illegal control characters" in res


def test_git_tool_validates_path_within_workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    tool = GitTool(ws)

    res = tool.execute(subcommand="diff", file_path="../outside.txt")
    assert "Path error:" in res
    assert "outside workspace" in res


def test_git_tool_constructed_argv_execution(tmp_path: Path):
    ws = Workspace(tmp_path)
    tool = GitTool(ws)

    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "## main...origin/main\n M foo.py\n"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        out = tool.execute(subcommand="status", args=["--short"])
        assert "## main...origin/main" in out

        # Verify argv was constructed and shell=False was enforced
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[1] == "--no-pager"
        assert cmd[2] == "status"
        assert "--short" in cmd
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == str(tmp_path)


def test_git_tool_output_truncation(tmp_path: Path):
    ws = Workspace(tmp_path)
    tool = GitTool(ws)

    huge_output = "a" * 60_000
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = huge_output
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        out = tool.execute(subcommand="diff")
        assert len(out) < 55_000
        assert "Output truncated" in out
