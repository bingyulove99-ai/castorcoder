"""Interactive conversation picker used by /resume."""

from __future__ import annotations

import sys
import types

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from castorcoder import picker
from castorcoder.picker import (
    _run_app,
    _run_select,
    pick_effort,
    pick_session,
    session_lines,
    short_time,
    truncate_text,
    workspace_name,
)
from castorcoder.reasoning import EFFORT_LEVELS


def _sessions() -> list[dict]:
    return [
        {
            "id": "session_one",
            "model": "deepseek-flash",
            "saved_at": "2026-11-12 14:03:22",
            "preview": "fix the failing test in tests/test_foo.py",
            "turns": 3,
            "workspace": "C:\\Users\\me\\CastorCoder",
        },
        {
            "id": "session_two",
            "model": "deepseek-flash",
            "saved_at": "2026-11-11 09:12:01",
            "preview": "what does this repository do?",
            "turns": 1,
            "workspace": "C:\\Users\\me\\Other",
        },
    ]


def test_picker_labels_are_compact():
    assert short_time("2026-11-12 14:03:22") == "11-12 14:03"
    assert short_time("") == "?"
    assert workspace_name("C:\\Users\\me\\CastorCoder") == "CastorCoder"
    assert truncate_text("a  b\nc", 20) == "a b c"
    assert truncate_text("abcdefgh", 6) == "abc..."


def test_session_lines_show_number_turns_and_preview():
    rows = session_lines(_sessions(), show_workspace=True, width=100)
    text = "".join(row[1] for row in rows)
    assert "1." in text and "2." in text
    assert "3 turns" in text
    assert "fix the failing test" in text
    assert "CastorCoder" in text


def test_pick_session_needs_sessions_and_a_terminal(monkeypatch):
    assert pick_session([]) is None
    monkeypatch.setattr(picker.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert pick_session(_sessions()) is None


def test_picker_arrow_keys_select_and_escape_cancels():
    with create_pipe_input() as keys:
        keys.send_text("j\r")
        chosen = _run_app(
            _sessions(),
            title="Resume a conversation",
            current_id=None,
            show_workspace=False,
            input=keys,
            output=DummyOutput(),
        )
    assert chosen is not None and chosen["id"] == "session_two"

    with create_pipe_input() as keys:
        keys.send_text("\x1b")
        cancelled = _run_app(
            _sessions(),
            title="Resume a conversation",
            current_id=None,
            show_workspace=False,
            input=keys,
            output=DummyOutput(),
        )
    assert cancelled is None


def test_picker_opens_on_the_current_session():
    with create_pipe_input() as keys:
        keys.send_text("\r")
        chosen = _run_app(
            _sessions(),
            title="Resume a conversation",
            current_id="session_two",
            show_workspace=False,
            input=keys,
            output=DummyOutput(),
        )
    assert chosen is not None and chosen["id"] == "session_two"


def test_run_select_arrow_keys_return_an_index():
    with create_pipe_input() as keys:
        keys.send_text("j\r")
        chosen = _run_select(
            [("auto", "provider default"), ("high", "deeper reasoning")],
            title="Thinking effort",
            hint="up/down move",
            input=keys,
            output=DummyOutput(),
        )
    assert chosen == 1


def test_pick_effort_uses_the_shared_selector(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    observed = {}

    def fake_select(entries, **kwargs):
        observed["levels"] = [entry[0] for entry in entries]
        observed["initial"] = kwargs["initial"]
        return 2

    monkeypatch.setattr(picker, "_run_select", fake_select)
    assert pick_effort("off") == "low"
    assert observed["levels"] == list(EFFORT_LEVELS)
    assert observed["initial"] == 1


def test_pick_effort_falls_back_to_numbers_and_can_cancel(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        picker,
        "_run_select",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no tty")),
    )
    monkeypatch.setattr("builtins.input", lambda *args: "4")
    assert pick_effort() == "medium"

    monkeypatch.setattr("builtins.input", lambda *args: "")
    assert pick_effort() is None

    monkeypatch.setattr("builtins.input", lambda *args: "99")
    assert pick_effort() is None


def test_pick_effort_needs_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert pick_effort() is None


def test_picker_ignores_terminal_relaunch_failures(monkeypatch):
    emitted: list[str] = []

    class _Console:
        width = 90

        def print(self, *args, **kwargs):
            emitted.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        picker,
        "_run_app",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no tty")),
    )
    monkeypatch.setattr("builtins.input", lambda *args: "2")
    assert pick_session(_sessions(), console=_Console())["id"] == "session_two"
    assert any("1." in line for line in emitted)
