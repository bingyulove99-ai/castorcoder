"""Terminal rendering: bounded reasoning and event-driven status."""

from __future__ import annotations

import io

from rich.console import Console

from castorcoder.events import AgentEvent
from castorcoder.ui import LiveRenderer


def _renderer(**kwargs) -> tuple[LiveRenderer, io.StringIO]:
    stream = io.StringIO()
    console = Console(file=stream, width=120, force_terminal=False, color_system=None)
    return LiveRenderer(console, status=False, **kwargs), stream


def test_brief_reasoning_shows_a_prefix_then_hides_the_rest():
    renderer, stream = _renderer(reasoning="brief", reasoning_chars=10)
    renderer.begin()
    renderer.on_reasoning("0123456789ABCDEF")
    renderer.on_reasoning("more reasoning that must not be printed")
    renderer.on_token("answer")
    renderer.finish()

    output = stream.getvalue()
    assert "0123456789" in output
    assert "ABCDEF" not in output
    assert "reasoning hidden" in output
    assert "answer" in output


def test_full_reasoning_is_streamed_and_separated_from_the_answer():
    renderer, stream = _renderer(reasoning="full")
    renderer.begin()
    renderer.on_reasoning("full chain of thought")
    renderer.on_token("answer")
    renderer.finish()
    output = stream.getvalue()
    assert "full chain of thought" in output
    assert "answer" in output


def test_reasoning_off_prints_nothing():
    renderer, stream = _renderer(reasoning="off")
    renderer.begin()
    renderer.on_reasoning("secret chain of thought")
    renderer.on_token("answer")
    renderer.finish()
    output = stream.getvalue()
    assert "secret" not in output
    assert "answer" in output


def test_events_use_the_event_type_field():
    renderer, stream = _renderer(reasoning="off")
    renderer.begin()
    renderer.on_event(AgentEvent("tool.started", "r1", data={
        "tool_call_id": "c1", "name": "read_file", "arguments": {"file_path": "a.py"},
    }))
    renderer.on_event(AgentEvent("tool.completed", "r1", data={
        "tool_call_id": "c1", "name": "read_file", "success": True, "duration_ms": 12,
    }))
    renderer.finish()
    output = stream.getvalue()
    assert "read_file" in output
    assert "ok" in output


def test_answer_markdown_is_formatted_while_streaming():
    renderer, stream = _renderer(reasoning="off")
    renderer.begin()
    for chunk in [
        "## Summary\n\n",
        "The bug is in `validate_path`.\n\n",
        "- joins untrusted input\n- never resolves symlinks\n\n",
        "```python\nvalue = resolve(path)\n```\n\n",
        "Done.\n",
    ]:
        renderer.on_token(chunk)
    renderer.finish()

    output = stream.getvalue()
    assert "Summary" in output
    assert "##" not in output
    assert "```" not in output
    assert "`" not in output
    assert "value = resolve(path)" in output
    assert "Done." in output


def test_markdown_blocks_split_on_paragraphs_and_fences():
    from castorcoder.ui import split_markdown_blocks

    blocks, remainder = split_markdown_blocks("first paragraph\n\n```python\nx = 1\n")
    assert blocks == ["first paragraph"]
    assert remainder.startswith("```python")

    blocks, remainder = split_markdown_blocks("```python\nx = 1\n```\n\ntail")
    assert blocks == ["```python\nx = 1\n```"]
    assert remainder == "tail"

    blocks, remainder = split_markdown_blocks("no boundary yet")
    assert blocks == [] and remainder == "no boundary yet"


def test_blocks_are_separated_and_tool_receipts_are_one_line():
    renderer, stream = _renderer(reasoning="off")
    renderer.begin()
    renderer.on_event(AgentEvent("tool.started", "r", data={
        "tool_call_id": "c1", "name": "read_file", "arguments": {"file_path": "a.py"},
    }))
    renderer.on_event(AgentEvent("tool.completed", "r", data={
        "tool_call_id": "c1", "name": "read_file", "success": True, "duration_ms": 12,
    }))
    renderer.on_token("First paragraph.\n\nSecond paragraph.\n")
    renderer.on_event(AgentEvent("tool.started", "r", data={
        "tool_call_id": "c2", "name": "bash", "arguments": {"command": "pytest -q"},
    }))
    renderer.on_event(AgentEvent("tool.completed", "r", data={
        "tool_call_id": "c2", "name": "bash", "success": False, "duration_ms": 1200,
    }))
    renderer.finish()

    output = stream.getvalue()
    tool_lines = [line for line in output.splitlines() if "read_file" in line]
    assert len(tool_lines) == 1
    assert "12ms" in tool_lines[0]
    assert "-> " not in output
    assert "FAIL" in output and "1.2s" in output
    # Paragraphs are separated instead of being glued together.
    stripped = [line.rstrip() for line in output.splitlines()]
    first = stripped.index("First paragraph.")
    assert stripped[first + 1] == ""
    assert stripped[first + 2] == "Second paragraph."
