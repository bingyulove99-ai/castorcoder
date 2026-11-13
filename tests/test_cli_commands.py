"""Operational CLI command tests using a real Agent and local stores."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from castorcoder import cli
from castorcoder.agent import Agent
from castorcoder.config import Config
from castorcoder.evals.runner import ScriptedLLM
from castorcoder.index import CodeIndex
from castorcoder.memory import MemoryStore


def _agent(tmp_path):
    (tmp_path / "app.py").write_text(
        "class Workspace:\n"
        "    def validate_path(self, candidate):\n"
        "        return candidate\n",
        encoding="utf-8",
    )
    store = MemoryStore(tmp_path / "memory.db", workspace=str(tmp_path.resolve()))
    index = CodeIndex(tmp_path, db_path=tmp_path / "db" / "index.db", auto_build=False)
    agent = Agent(
        ScriptedLLM([]),
        workspace=tmp_path,
        memory_store=store,
        code_index=index,
    )
    return agent


def test_cli_index_search_and_memory_commands(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))

    assert cli._handle_command("/index", agent, config, None)
    assert "Index updated" in capsys.readouterr().out

    assert cli._handle_command("/search validate path", agent, config, None)
    assert "app.py" in capsys.readouterr().out

    agent.memory_store.add("Always run pytest.", kind="preference")
    assert cli._handle_command("/memory search pytest", agent, config, None)
    assert "pytest" in capsys.readouterr().out
    assert cli._handle_command("/memory stats", agent, config, None)
    assert "active" in capsys.readouterr().out
    assert cli._handle_command("/memory list", agent, config, None)
    assert "pytest" in capsys.readouterr().out
    assert cli._handle_command("/memory gc", agent, config, None)
    assert "Garbage-collected" in capsys.readouterr().out


def test_cli_operational_commands(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))

    assert cli._handle_command("/help", agent, config, None)
    assert cli._handle_command("/tools", agent, config, None)
    assert cli._handle_command("/skills", agent, config, None)
    assert cli._handle_command("/tasks", agent, config, None)
    assert cli._handle_command("/status", agent, config, None)
    assert cli._handle_command("/tokens", agent, config, None)
    assert cli._handle_command("/model", agent, config, None)
    assert cli._handle_command("/model new-model", agent, config, None)
    assert agent.llm.model == "new-model"
    assert cli._handle_command("/compact", agent, config, None)
    assert cli._handle_command("/diff", agent, config, None)
    assert cli._handle_command("/trace", agent, config, None)
    assert cli._handle_command("/config", agent, config, None)
    assert cli._handle_command("/reset", agent, config, None)
    assert not cli._handle_command("/unknown", agent, config, None)
    capsys.readouterr()


def test_compact_command_acknowledges_and_compresses(tmp_path, capsys):
    from castorcoder.context import estimate_tokens

    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))

    assert cli._handle_command("/compact", agent, config, None)
    assert "too short" in capsys.readouterr().out

    assert cli._handle_command("/compact zero", agent, config, None)
    assert "Usage: /compact" in capsys.readouterr().out

    for index in range(5):
        agent.messages.append({"role": "user", "content": "x" * 4000 + str(index)})
        agent.messages.append({"role": "assistant", "content": "noted " + str(index)})
    before = estimate_tokens(agent.messages)
    assert cli._handle_command("/compact", agent, config, None)
    out = capsys.readouterr().out
    assert "Compacting" in out
    assert "Compacted" in out
    assert estimate_tokens(agent.messages) < before
    assert len(agent.messages) == 6


def test_command_normalisation_accepts_ime_slash_and_case():
    assert cli._normalise_command("／compact") == "/compact"
    assert cli._normalise_command("  /COMPACT  ") == "/compact"
    assert cli._normalise_command("/search FooBar") == "/search FooBar"
    assert cli._normalise_command("普通消息") == "普通消息"


def test_cli_undo_and_disabled_stores(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path), index=False, memory=False)
    write_tool = next(tool for tool in agent.tools if tool.name == "write_file")
    write_tool.execute(file_path="created.txt", content="x")
    assert cli._handle_command("/undo", agent, config, None)
    assert not (tmp_path / "created.txt").exists()
    capsys.readouterr()

    plain = Agent(ScriptedLLM([]), workspace=tmp_path)
    assert cli._handle_command("/index", plain, config, None)
    assert "disabled" in capsys.readouterr().out
    assert cli._handle_command("/index --force", plain, config, None)
    assert "disabled" in capsys.readouterr().out
    assert cli._handle_command("/search anything", plain, config, None)
    assert "disabled" in capsys.readouterr().out
    assert cli._handle_command("/search ", plain, config, None)
    assert "disabled" in capsys.readouterr().out
    assert cli._handle_command("/memory stats", plain, config, None)
    assert "disabled" in capsys.readouterr().out


def test_cli_memory_subcommands_and_save(tmp_path, monkeypatch, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    record, _ = agent.memory_store.add("Remember this fact.", kind="fact")
    assert cli._handle_command("/memory", agent, config, None)
    assert "Remember this fact" in capsys.readouterr().out
    assert cli._handle_command(f"/memory forget {record.id}", agent, config, None)
    assert "Forgotten" in capsys.readouterr().out
    assert cli._handle_command("/memory forget missing", agent, config, None)
    assert "not found" in capsys.readouterr().out
    assert cli._handle_command("/memory search", agent, config, None)
    assert "Usage" in capsys.readouterr().out
    assert cli._handle_command("/memory nope", agent, config, None)
    assert "Usage" in capsys.readouterr().out

    monkeypatch.setattr(cli, "save_session", lambda *args, **kwargs: "session-test")
    assert cli._handle_command("/save", agent, config, None)
    assert "session-test" in capsys.readouterr().out
    monkeypatch.setattr(cli, "list_sessions", lambda: [])
    assert cli._handle_command("/sessions", agent, config, None)
    assert "No saved sessions" in capsys.readouterr().out
    assert cli._handle_command("/trace", agent, config, tmp_path / "trace.jsonl")
    # rich may wrap a long Windows path between "trace." and "jsonl".
    assert "trace.jsonl" in capsys.readouterr().out.replace("\n", "")


def test_auto_save_binds_workspace_and_marks_latest(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    agent.messages = [{"role": "user", "content": "hello"}]
    agent.session_id = "session-auto"
    config = Config(workspace=str(tmp_path))
    observed = {}

    def fake_save(messages, model, session_id=None, metadata=None):
        observed.update(messages=messages, model=model, session_id=session_id, metadata=metadata)
        return session_id or "generated"

    monkeypatch.setattr(cli, "save_session", fake_save)
    monkeypatch.setattr(
        cli, "mark_last_session", lambda workspace, session_id: observed.update(
            marked=(str(workspace), session_id)
        )
    )

    session_id = cli._save_agent_session(agent, config)
    assert session_id == "session-auto"
    assert observed["metadata"]["workspace"] == str(tmp_path.resolve())
    assert observed["marked"][1] == "session-auto"
    assert agent.session_id == "session-auto"


def test_reasoning_and_session_commands(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))

    assert cli._handle_command("/reasoning brief", agent, config, None)
    assert config.reasoning_mode == "brief"
    assert cli._handle_command("/reasoning full", agent, config, None)
    assert config.reasoning_mode == "full"
    old_session = agent.session_id
    assert cli._handle_command("/new", agent, config, None)
    assert agent.session_id != old_session
    assert cli._handle_command("/session", agent, config, None)
    assert agent.session_id in capsys.readouterr().out


def test_effort_command_sets_the_thinking_level(tmp_path, capsys, monkeypatch):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    applied: list[str] = []
    agent.llm.set_reasoning_effort = applied.append

    assert cli._handle_command("/effort high", agent, config, None)
    assert config.reasoning_effort == "high"
    assert applied == ["high"]
    assert "Thinking effort" in capsys.readouterr().out

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli._handle_command("/effort", agent, config, None)
    output = capsys.readouterr().out
    assert "auto" in output and "max" in output and "high" in output
    assert "Current thinking effort" in output

    assert cli._handle_command("/effort minimal", agent, config, None)
    assert config.reasoning_effort == "low"
    capsys.readouterr()

    assert cli._handle_command("/effort turbo", agent, config, None)
    assert "Usage: /effort" in capsys.readouterr().out


def test_effort_command_opens_the_picker(monkeypatch, tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "pick_effort", lambda current, console=None: "max")

    assert cli._handle_command("/effort", agent, config, None)
    assert config.reasoning_effort == "max"
    assert "max" in capsys.readouterr().out


def test_resume_target_prefers_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "latest_session", lambda workspace=None: {"id": "from-workspace"})
    args = cli._parse_args(["--continue"])
    assert cli._resume_target(args, tmp_path) == "from-workspace"


def test_cli_index_force_and_empty_search(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    assert cli._handle_command("/index --force", agent, config, None)
    assert "Index updated" in capsys.readouterr().out
    assert cli._handle_command("/search ", agent, config, None)
    assert "Usage" in capsys.readouterr().out


def _write_session(directory, session_id, content, *, workspace, ts):
    import json

    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "id": session_id,
        "model": "deepseek-flash",
        "saved_at": "2026-11-12 10:00:00",
        "saved_at_ts": ts,
        "messages": [{"role": "user", "content": content}],
        "metadata": {"workspace": str(workspace)},
    }
    (directory / f"{session_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _resume_fixture(tmp_path, monkeypatch):
    from castorcoder import session as session_module

    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(session_module, "SESSIONS_DIR", sessions_dir)
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    workspace = tmp_path.resolve()
    _write_session(sessions_dir, "session_old", "old question", workspace=workspace, ts=1000)
    _write_session(sessions_dir, "session_new", "new question", workspace=workspace, ts=2000)
    return agent, config


def test_resume_command_restores_a_numbered_session(tmp_path, monkeypatch, capsys):
    agent, config = _resume_fixture(tmp_path, monkeypatch)

    assert cli._handle_command("/resume 2", agent, config, None)
    assert agent.session_id == "session_old"
    output = capsys.readouterr().out
    assert "Resumed" in output
    assert "old question" in output


def test_resume_command_out_of_range_number(tmp_path, monkeypatch, capsys):
    agent, config = _resume_fixture(tmp_path, monkeypatch)

    assert cli._handle_command("/resume 9", agent, config, None)
    assert "No session #9" in capsys.readouterr().out


def test_resume_command_opens_the_picker(tmp_path, monkeypatch):
    agent, config = _resume_fixture(tmp_path, monkeypatch)
    seen = {}

    def fake_pick(sessions, **kwargs):
        seen["ids"] = [item["id"] for item in sessions]
        seen["current"] = kwargs.get("current_id")
        return sessions[1]

    monkeypatch.setattr(cli, "pick_session", fake_pick)
    before = agent.session_id
    assert cli._handle_command("/resume", agent, config, None)
    assert seen["ids"] == ["session_new", "session_old"]
    assert seen["current"] == before
    assert agent.session_id == "session_old"


def test_resume_command_cancel_keeps_the_current_session(tmp_path, monkeypatch, capsys):
    agent, config = _resume_fixture(tmp_path, monkeypatch)
    agent.session_id = "session_current"
    monkeypatch.setattr(cli, "pick_session", lambda *args, **kwargs: None)

    assert cli._handle_command("/resume", agent, config, None)
    assert agent.session_id == "session_current"
    assert "cancelled" in capsys.readouterr().out.lower()


def test_sessions_command_renders_a_numbered_table(tmp_path, monkeypatch, capsys):
    agent, _config = _resume_fixture(tmp_path, monkeypatch)
    agent.session_id = "session_new"

    assert cli._handle_command("/sessions", agent, Config(workspace=str(tmp_path)), None)
    output = capsys.readouterr().out
    assert "old question" in output and "new question" in output
    assert "* = current session" in output


def test_bare_resume_uses_latest_outside_a_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "list_sessions", lambda limit=50, workspace=None: [{"id": "newest"}])
    args = cli._parse_args(["--resume"])
    assert args.resume == "__pick__"
    assert cli._resume_target(args, tmp_path) == "newest"


def test_rounds_command_shows_and_updates_the_limit(tmp_path, capsys):
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))
    assert agent.max_rounds == 200

    assert cli._handle_command("/rounds", agent, config, None)
    assert "200" in capsys.readouterr().out

    assert cli._handle_command("/rounds 350", agent, config, None)
    assert agent.max_rounds == 350
    assert config.max_rounds == 350
    capsys.readouterr()

    assert cli._handle_command("/rounds zero", agent, config, None)
    assert "Usage" in capsys.readouterr().out
    assert cli._handle_command("/rounds 5000", agent, config, None)
    assert "Usage" in capsys.readouterr().out
    assert agent.max_rounds == 350


def test_dashboard_command_reuses_the_background_cockpit(tmp_path, capsys, monkeypatch):
    server = MagicMock()
    server.server_address = ("127.0.0.1", 8123)

    class FakeThread:
        def __init__(self, target=None, **kwargs):
            self.target = target
            self.daemon = kwargs.get("daemon", False)

        def start(self):
            return None

    monkeypatch.setattr(cli.threading, "Thread", FakeThread)
    monkeypatch.setitem(cli._DASHBOARD, "server", None)
    monkeypatch.setitem(cli._DASHBOARD, "url", "")
    agent = _agent(tmp_path)
    config = Config(workspace=str(tmp_path))

    with patch(
        "castorcoder.dashboard.server.start_dashboard", return_value=server
    ) as start:
        assert cli._handle_command("/dashboard 8123", agent, config, None)
        assert "8123" in capsys.readouterr().out
        start.assert_called_once()
        assert start.call_args.kwargs["server_root"] == tmp_path.resolve()
        assert start.call_args.kwargs["workspace_root"] == tmp_path.resolve()

        assert cli._handle_command("/dashboard", agent, config, None)
        assert start.call_count == 1
        capsys.readouterr()

    with patch(
        "castorcoder.dashboard.server.start_dashboard", side_effect=OSError("in use")
    ):
        monkeypatch.setitem(cli._DASHBOARD, "server", None)
        monkeypatch.setitem(cli._DASHBOARD, "url", "")
        assert cli._handle_command("/dashboard", agent, config, None)
        assert "could not bind" in capsys.readouterr().out
