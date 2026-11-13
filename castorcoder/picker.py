"""Interactive conversation picker used by ``/resume`` and ``--resume``.

Claude Code's ``/resume`` lists past conversations, lets the user move with
arrow keys and restores the highlighted one.  CastorCoder previously only
accepted an explicit session id, so the user had to run ``/sessions`` first
and copy a timestamp-like identifier.

The picker is a small prompt_toolkit application, so it works in the same
terminals as the interactive prompt.  When prompt_toolkit cannot host the
application, it degrades to a numbered prompt; when stdin is not a terminal at
all it returns ``None`` and the caller decides what to do.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from typing import Any

from rich.console import Console

from .reasoning import EFFORT_LEVELS, EFFORT_SUMMARY, normalise_effort

_ENTRY_ROWS = 5


def short_time(value: Any) -> str:
    """``2026-11-12 14:03:22`` -> ``11-12 14:03`` (keeps tables narrow)."""

    text = str(value or "").strip()
    date, _, clock = text.partition(" ")
    parts = date.split("-")
    if clock and len(parts) == 3:
        return f"{parts[1]}-{parts[2]} {clock[:5]}"
    return text[:16] or "?"


def workspace_name(value: Any) -> str:
    text = str(value or "").strip().rstrip("/\\")
    return text.replace("\\", "/").rsplit("/", 1)[-1] if text else ""


def truncate_text(text: Any, limit: int) -> str:
    collapsed = " ".join(str(text or "").split())
    if limit <= 3 or len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _heading(session: dict, *, show_workspace: bool = False) -> str:
    bits = [
        short_time(session.get("saved_at")),
        f"{session.get('turns', '?')} turns",
        str(session.get("model") or "?"),
    ]
    if show_workspace:
        name = workspace_name(session.get("workspace"))
        if name:
            bits.append(name)
    return "   ".join(bits)


def session_lines(
    sessions: Sequence[dict],
    *,
    current_id: str | None = None,
    show_workspace: bool = False,
    width: int = 80,
    start: int = 0,
    limit: int | None = None,
) -> list[tuple[str, str]]:
    """Return ``(style, text)`` rows for a session list.

    Kept separate from the prompt_toolkit application so the layout can be
    tested and reused by the numbered fallback.
    """

    rows: list[tuple[str, str]] = []
    end = len(sessions) if limit is None else min(len(sessions), start + limit)
    for position in range(start, end):
        session = sessions[position]
        heading = _heading(session, show_workspace=show_workspace)
        if current_id and session.get("id") == current_id:
            heading += "   (current)"
        rows.append(("entry", f"{position + 1:>2}. {heading}"))
        rows.append(
            ("preview", f"    {truncate_text(session.get('preview'), width - 8)}")
        )
    return rows


def pick_session(
    sessions: Sequence[dict],
    *,
    console: Console | None = None,
    title: str = "Resume a conversation",
    current_id: str | None = None,
    show_workspace: bool = False,
) -> dict | None:
    """Let the user choose one saved session. Returns the entry or ``None``."""

    sessions = list(sessions)
    if not sessions or not sys.stdin.isatty():
        return None
    try:
        return _run_app(
            sessions,
            title=title,
            current_id=current_id,
            show_workspace=show_workspace,
        )
    except Exception:
        try:
            return _numbered_pick(
                sessions,
                console=console,
                title=title,
                current_id=current_id,
                show_workspace=show_workspace,
            )
        except Exception:
            return None


def _run_select(
    entries: Sequence[tuple[str, str]],
    *,
    title: str,
    hint: str,
    initial: int = 0,
    page_size: int = _ENTRY_ROWS,
    input=None,
    output=None,
) -> int | None:
    """Arrow-key single-choice list; returns the chosen index or ``None``.

    ``entries`` are ``(heading, detail)`` pairs.  The same application serves
    the conversation picker and the thinking-effort picker.
    """

    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    entries = list(entries)
    if not entries:
        return None
    initial = max(0, min(len(entries) - 1, initial))
    state = {"index": initial, "offset": 0}

    def keep_visible() -> None:
        if state["index"] < state["offset"]:
            state["offset"] = state["index"]
        elif state["index"] >= state["offset"] + page_size:
            state["offset"] = state["index"] - page_size + 1

    def move(delta: int) -> None:
        state["index"] = max(0, min(len(entries) - 1, state["index"] + delta))
        keep_visible()

    def fragments() -> list[tuple[str, str]]:
        width = max(60, shutil.get_terminal_size((100, 30)).columns - 4)
        rows: list[tuple[str, str]] = [
            ("class:title", f" {title}\n"),
            ("class:hint", f" {hint}\n\n"),
        ]
        end = min(len(entries), state["offset"] + page_size)
        for position in range(state["offset"], end):
            heading, detail = entries[position]
            selected = position == state["index"]
            style = "class:selected" if selected else "class:entry"
            marker = ">" if selected else " "
            rows.append((style, f"{marker} {position + 1:>2}. {heading}\n"))
            if detail:
                rows.append((style, f"      {truncate_text(detail, width - 8)}\n"))
        if len(entries) > page_size:
            rows.append(
                (
                    "class:hint",
                    f"\n {state['offset'] + 1}-{end} of {len(entries)}   (page up/down jumps)",
                )
            )
        return rows

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event):
        move(-1)

    @bindings.add("down")
    @bindings.add("j")
    def _down(event):
        move(1)

    @bindings.add("pageup")
    def _page_up(event):
        move(-page_size)

    @bindings.add("pagedown")
    def _page_down(event):
        move(page_size)

    @bindings.add("home")
    def _home(event):
        state["index"] = 0
        keep_visible()

    @bindings.add("end")
    def _end(event):
        state["index"] = len(entries) - 1
        keep_visible()

    @bindings.add("enter")
    def _accept(event):
        event.app.exit(result=state["index"])

    @bindings.add("escape")
    @bindings.add("c-c")
    @bindings.add("q")
    def _cancel(event):
        event.app.exit(result=None)

    style = Style.from_dict(
        {
            "title": "bold",
            "hint": "fg:#888888",
            "entry": "",
            "selected": "reverse",
            "preview": "fg:#888888",
        }
    )

    application = Application(
        layout=Layout(
            Window(
                FormattedTextControl(fragments, focusable=True),
                dont_extend_height=True,
                always_hide_cursor=True,
            )
        ),
        key_bindings=bindings,
        style=style,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
        input=input,
        output=output,
    )
    return application.run()


def _run_app(
    sessions: list[dict],
    *,
    title: str,
    current_id: str | None,
    show_workspace: bool,
    input=None,
    output=None,
) -> dict | None:
    sessions = list(sessions)
    if not sessions:
        return None
    initial = next(
        (
            index
            for index, item in enumerate(sessions)
            if current_id and item.get("id") == current_id
        ),
        0,
    )
    entries: list[tuple[str, str]] = []
    for session in sessions:
        heading = _heading(session, show_workspace=show_workspace)
        if current_id and session.get("id") == current_id:
            heading += "   (current)"
        entries.append((heading, str(session.get("preview") or "")))
    index = _run_select(
        entries,
        title=title,
        hint="up/down move   enter resume   esc cancel",
        initial=initial,
        input=input,
        output=output,
    )
    return sessions[index] if index is not None else None


def pick_effort(
    current: str = "auto",
    *,
    console: Console | None = None,
    title: str = "Thinking effort",
) -> str | None:
    """Pick a thinking level with arrow keys; ``None`` when cancelled.

    Falls back to a numbered prompt when prompt_toolkit cannot host the
    application, and returns ``None`` immediately when stdin is not a TTY.
    """

    if not sys.stdin.isatty():
        return None
    levels = list(EFFORT_LEVELS)
    entries = [(level, EFFORT_SUMMARY[level]) for level in levels]
    current = normalise_effort(current)
    try:
        index = _run_select(
            entries,
            title=title,
            hint="up/down move   enter select   esc cancel",
            initial=levels.index(current),
            page_size=len(levels),
        )
    except Exception:
        try:
            return _numbered_choice(levels, console=console, title=title, current=current)
        except Exception:
            return None
    return levels[index] if index is not None else None


def _numbered_choice(
    values: Sequence[str],
    *,
    console: Console | None,
    title: str,
    current: str,
) -> str | None:
    console = console or Console()
    console.print(f"[bold]{title}[/bold]")
    for position, value in enumerate(values, start=1):
        marker = " (current)" if value == current else ""
        console.print(f"{position:>2}. {value}{marker}   {EFFORT_SUMMARY.get(value, '')}")
    try:
        answer = input("Choose # (empty cancels) > ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer.isdigit():
        return None
    index = int(answer) - 1
    return values[index] if 0 <= index < len(values) else None


def _numbered_pick(
    sessions: list[dict],
    *,
    console: Console | None,
    title: str,
    current_id: str | None,
    show_workspace: bool,
) -> dict | None:
    console = console or Console()
    console.print(f"[bold]{title}[/bold]")
    for row_style, text in session_lines(
        sessions,
        current_id=current_id,
        show_workspace=show_workspace,
        width=console.width,
    ):
        console.print(text, style="dim" if row_style == "preview" else None, markup=False)
    try:
        answer = input("Resume # (empty cancels) > ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer.isdigit():
        return None
    index = int(answer) - 1
    return sessions[index] if 0 <= index < len(sessions) else None
