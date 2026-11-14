"""Live terminal rendering: status line, compact tools, formatted answers.

Claude Code answers the question "is it working?" with a moving status line
and short tool receipts; the model's private reasoning is not dumped to the
screen.  CastorCoder previously printed every reasoning token and every tool
event with a separate ``print`` call, which was both noisy and slow.

The second problem was typography: streamed answer text was written with
``markup=False`` so the model's Markdown reached the terminal as raw source
(``## heading``, ``- item``, triple backticks), and every block ran into the
next one.  This module now:

* buffers answer tokens and renders *complete* Markdown blocks through
  ``rich.markdown.Markdown`` as soon as a paragraph or code fence closes, so
  the text stays streamed but is formatted;
* keeps one refreshing status line (``rich.live.Live``) while the model or a
  tool is working;
* prints one aligned receipt line per tool call and result;
* separates answer, tool and reasoning blocks with a single blank line;
* bounds reasoning: ``brief`` shows a short prefix then hides the rest,
  ``full`` streams it dimmed, ``off`` shows nothing.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from typing import Any

from rich.cells import cell_len
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

# The startup mark: a beaver (castor) with terminal eyes and buck teeth.
# Pure ASCII on purpose - a GBK console can print it without a UTF-8 switch.
BRAND_ICON = (
    r"      .-.        .-.",
    r"     /   \______/   \ ",
    r"    |   (o)    (o)   |",
    r"    |        __       |",
    r"     \      /  \     /",
    r"      '----'    '---'",
    r"           |_||_|",
)

# Cyan -> violet gradient, one colour per icon line.
_BRAND_COLORS = (
    "#22d3ee",
    "#38bdf8",
    "#60a5fa",
    "#818cf8",
    "#a78bfa",
    "#c084fc",
    "#e879f9",
)


def brand_mark() -> Text:
    """Return the CastorCoder startup mark as a styled rich ``Text``."""

    mark = Text()
    for index, line in enumerate(BRAND_ICON):
        if index:
            mark.append("\n")
        mark.append(line.rstrip(), style=f"bold {_BRAND_COLORS[index]}")
    return mark


# ASCII only: a legacy Windows console (GBK/cp936) cannot encode braille or
# most emoji, and rich's legacy renderer raises instead of replacing them.
_SPINNER = "-\\|/"
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LIST_RE = re.compile(r"^([-*+]|\d+[.)])\s")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_BLOCK_CHARS = 700

# Rich Markdown leaves a leading blank line before these blocks but not
# before headings or paragraphs, so the renderer only adds its own separator
# where the renderable does not already provide one.
_SELF_LEADING = {
    "fence": True,
    "list": True,
    "quote": True,
    "table": True,
    "heading": False,
    "paragraph": False,
}


def _brief_argument(arguments: Any, limit: int = 90) -> str:
    if not isinstance(arguments, dict):
        return ""
    parts = []
    for key, value in arguments.items():
        text = repr(value)
        if len(text) > 48:
            text = text[:45] + "..."
        parts.append(f"{key}={text}")
    joined = ", ".join(parts)
    return joined if len(joined) <= limit else joined[: limit - 3] + "..."


def _block_kind(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if _FENCE_RE.match(first):
        return "fence"
    if _HEADING_RE.match(first):
        return "heading"
    if _LIST_RE.match(first):
        return "list"
    if first.startswith(">"):
        return "quote"
    if first.startswith("|"):
        return "table"
    return "paragraph"


def _open_fence(text: str) -> bool:
    """True when ``text`` ends inside an unclosed code fence."""

    return sum(1 for line in text.splitlines() if _FENCE_RE.match(line)) % 2 == 1


def split_markdown_blocks(text: str) -> tuple[list[str], str]:
    """Split completed Markdown blocks from the streamed remainder.

    A block ends at a blank line outside a code fence, or right after a fence
    closes.  The remainder is the unfinished tail, which the caller keeps
    buffering so a block is never rendered half-written.
    """

    blocks: list[str] = []
    start = 0
    position = 0
    in_fence = False
    length = len(text)
    while position < length:
        line_end = text.find("\n", position)
        if line_end == -1:
            break
        line = text[position:line_end]
        if _FENCE_RE.match(line):
            if in_fence:
                in_fence = False
                block = text[start : line_end + 1].strip("\n")
                if block.strip():
                    blocks.append(block)
                start = line_end + 1
            else:
                in_fence = True
        elif not in_fence and not line.strip():
            block = text[start:line_end].strip("\n")
            if block.strip():
                blocks.append(block)
            start = line_end + 1
        position = line_end + 1
    return blocks, text[start:]


def _tail_boundary(text: str) -> int:
    """Index just past the last complete line, if it is safe to render."""

    if _open_fence(text):
        return 0
    cut = text.rfind("\n")
    return cut + 1 if cut > 0 else 0


def _format_duration(duration_ms: float) -> str:
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{duration_ms:.0f}ms"


class _StatusView:
    """A renderable that shows a spinner plus the current phase."""

    def __init__(self, renderer: _TerminalRenderer) -> None:
        self.renderer = renderer

    def __rich_console__(self, console: Console, options):
        frame = _SPINNER[int(time.perf_counter() * 10) % len(_SPINNER)]
        yield Text(self.renderer.status_line(frame), style="dim cyan")


class _TerminalRenderer:
    """Consume agent callbacks/events and keep the terminal legible."""

    def __init__(
        self,
        console: Console | None = None,
        *,
        reasoning: str = "brief",
        reasoning_chars: int = 220,
        status: bool | None = None,
    ) -> None:
        self.console = console or Console()
        self.reasoning = reasoning if reasoning in {"brief", "full", "off"} else "brief"
        self.reasoning_chars = max(0, reasoning_chars)
        env_off = os.getenv("CASTORCODER_NO_STATUS", "").lower() in {"1", "true", "yes", "on"}
        self.status_enabled = (
            (self.console.is_terminal if status is None else status) and not env_off
        )
        self._lock = threading.RLock()
        self._live: Live | None = None
        self._phase = "Starting"
        self._detail = ""
        self._started = time.perf_counter()
        self._run_tokens = 0
        self._round = 0
        self._streamed_any = False
        self._md_buffer = ""
        self._reasoning_buffer = ""
        self._last_kind: str | None = None
        self._reasoning_printed = 0
        self._reasoning_total = 0
        self._reasoning_hidden = False
        self._reasoning_line_open = False
        self._tool_args: dict[str, dict] = {}
        self._prev_trailing_blank = False
        self._finished = False

    # ---- status line -------------------------------------------------
    def status_line(self, frame: str = "|") -> str:
        elapsed = time.perf_counter() - self._started
        bits = [self._phase]
        if self._detail:
            bits.append(self._detail)
        if self._run_tokens:
            bits.append(f"{self._run_tokens:,} tok")
        bits.append(f"{elapsed:.1f}s")
        return f"{frame} {' | '.join(bits)}"

    def _start_live(self) -> None:
        if not self.status_enabled or self._live is not None:
            return
        self._live = Live(
            _StatusView(self),
            console=self.console,
            transient=True,
            refresh_per_second=10,
            auto_refresh=True,
        )
        self._live.start()

    def _stop_live(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def _set_status(self, phase: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            if phase is not None:
                self._phase = phase
            if detail is not None:
                self._detail = detail

    def _pause(self) -> None:
        with self._lock:
            self._close_reasoning_locked()
            self._flush_markdown_locked(force=True)
            self._stop_live()

    def _resume(self) -> None:
        with self._lock:
            if not self._finished:
                self._start_live()

    # ---- markdown answer --------------------------------------------
    def _gap_before_locked(self, kind: str) -> None:
        """Insert one blank line between different kinds of output."""

        previous = self._last_kind
        if previous is None:
            return
        if previous == kind and kind != "content":
            # Consecutive tool receipts and note lines stay together.
            return
        if self._prev_trailing_blank and kind == "tool":
            # A rendered code fence already ends with a blank padding line.
            self._prev_trailing_blank = False
            return
        self.console.print()

    def _print_block_locked(self, block: str) -> None:
        text = block.strip("\n")
        if not text.strip():
            return
        kind = _block_kind(text)
        self._stop_live()
        previous = self._last_kind
        if previous in {"tool", "reasoning"}:
            needs_gap = not _SELF_LEADING[kind]
        elif previous == "content":
            needs_gap = not _SELF_LEADING[kind] and not self._prev_trailing_blank
        else:
            needs_gap = False
        if needs_gap:
            self.console.print()
        self.console.print(Markdown(text))
        self._last_kind = "content"
        self._prev_trailing_blank = kind == "fence"
        self._streamed_any = True

    def _flush_markdown_locked(self, force: bool = False) -> None:
        if not self._md_buffer:
            return
        blocks, remainder = split_markdown_blocks(self._md_buffer)
        if not blocks and len(remainder) >= _BLOCK_CHARS:
            cut = _tail_boundary(remainder)
            if cut:
                blocks = [remainder[:cut]]
                remainder = remainder[cut:]
        if force:
            if remainder.strip():
                blocks.append(remainder)
            remainder = ""
        self._md_buffer = remainder
        for block in blocks:
            self._print_block_locked(block)

    def on_token(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._streamed_any = True
            self._close_reasoning_locked()
            self._md_buffer += token
            self._flush_markdown_locked()
            self._set_status("Responding", "")
            self._start_live()

    # ---- bounded reasoning ------------------------------------------
    def on_reasoning(self, token: str) -> None:
        if not token or self.reasoning == "off":
            return
        with self._lock:
            self._reasoning_total += len(token)
            if self.reasoning == "full":
                # Everything is shown, so nothing is "hidden" at close.
                self._reasoning_printed = self._reasoning_total
                self._reasoning_buffer += token
                if "\n" in token or len(self._reasoning_buffer) >= 480:
                    self._stop_live()
                    chunk, self._reasoning_buffer = self._reasoning_buffer, ""
                    self._gap_before_locked("reasoning")
                    self.console.print(
                        chunk, style="dim", end="", highlight=False, markup=False
                    )
                    self._reasoning_line_open = True
                    self._last_kind = "reasoning"
                self._set_status("Thinking", f"{self._reasoning_total:,} chars")
                self._start_live()
                return
            # brief: print a short prefix, then stop and count the hidden rest.
            if self._reasoning_printed >= self.reasoning_chars:
                self._reasoning_hidden = True
                self._set_status("Thinking", f"{self._reasoning_total:,} chars hidden")
                return
            room = self.reasoning_chars - self._reasoning_printed
            shown = token[:room]
            if shown:
                self._stop_live()
                if not self._reasoning_line_open:
                    self._gap_before_locked("reasoning")
                    self.console.print("[dim]thinking[/dim] ", end="", highlight=False)
                    self._reasoning_line_open = True
                self.console.print(shown, style="dim", end="", highlight=False, markup=False)
                self._reasoning_printed += len(shown)
                self._last_kind = "reasoning"
            if len(token) > room:
                self._reasoning_hidden = True
                self._close_reasoning_locked()
            self._set_status("Thinking", "")
            self._start_live()

    def _close_reasoning_locked(self) -> None:
        if self._reasoning_buffer:
            self._stop_live()
            chunk, self._reasoning_buffer = self._reasoning_buffer, ""
            self.console.print(chunk, style="dim", end="", highlight=False, markup=False)
            self._reasoning_line_open = True
        if not self._reasoning_line_open:
            return
        hidden = max(0, self._reasoning_total - self._reasoning_printed)
        self._stop_live()
        if hidden:
            self.console.print(
                f" [dim]... (reasoning hidden: {hidden:,} more chars)[/dim]",
                end="\n",
            )
        else:
            self.console.print()
        self._reasoning_line_open = False
        self._last_kind = "reasoning"

    # ---- tool receipts ----------------------------------------------
    def _tool_receipt(self, name: str, arguments: dict, success: bool, duration_ms: float, cached: bool) -> Text:
        # Do not push durations to the far edge of a very wide terminal.
        width = min(self.console.width or 80, 120)
        mark = "ok" if success else "FAIL"
        tail_bits = []
        if cached:
            tail_bits.append("cached")
        if duration_ms:
            tail_bits.append(_format_duration(duration_ms))
        tail = "  ".join(tail_bits)

        head_plain = f"  {mark:<4} {name}"
        detail = _brief_argument(arguments)
        if detail:
            head_plain += f" {detail}"
        overflow = cell_len(head_plain) + cell_len(tail) + 2 - width
        if overflow > 0 and detail:
            keep = max(0, cell_len(detail) - overflow)
            detail = detail[:keep].rstrip(" ,") + ("..." if keep else "")
            head_plain = f"  {mark:<4} {name}" + (f" {detail}" if detail else "")
        pad = max(2, width - cell_len(head_plain) - cell_len(tail))

        text = Text()
        text.append(f"  {mark:<4}", style="green" if success else "bold red")
        text.append(f" {name}", style="cyan")
        if detail:
            text.append(f" {detail}", style="dim")
        text.append(" " * pad)
        text.append(tail, style="dim")
        return text

    def _emit_tool_locked(
        self,
        name: str,
        arguments: dict,
        success: bool,
        duration_ms: float,
        cached: bool,
    ) -> None:
        self._stop_live()
        self._gap_before_locked("tool")
        self.console.print(
            self._tool_receipt(name, arguments, success, duration_ms, cached),
            highlight=False,
        )
        self._last_kind = "tool"

    def _note_locked(self, text: Text) -> None:
        self._stop_live()
        self._gap_before_locked("tool")
        self.console.print(text, highlight=False)
        self._last_kind = "tool"

    # ---- events ------------------------------------------------------
    def on_event(self, event) -> None:
        kind = getattr(event, "type", "") or getattr(event, "name", "")
        data = getattr(event, "data", {}) or {}
        try:
            self._handle_event(kind, data)
        except Exception:
            # Rendering must never break an agent run.
            return

    def _handle_event(self, kind: str, data: dict) -> None:
        if kind == "run.started":
            self._started = time.perf_counter()
            self._set_status("Thinking", f"{data.get('model', '')}")
            self._resume()
        elif kind == "llm.started":
            self._round = int(data.get("round") or 1)
            self._set_status("Thinking", f"round {self._round}")
            self._resume()
        elif kind == "llm.completed":
            prompt = int(data.get("prompt_tokens") or 0)
            completion = int(data.get("completion_tokens") or 0)
            self._run_tokens += prompt + completion
            reason = int(data.get("reasoning_chars") or 0)
            if reason and self.reasoning != "off":
                self._reasoning_total = max(self._reasoning_total, reason)
        elif kind == "tool.started":
            call_id = str(data.get("tool_call_id") or "")
            name = str(data.get("name") or "tool")
            args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
            self._tool_args[call_id] = args
            with self._lock:
                self._pause()
            self._set_status("Running", name)
            self._resume()
        elif kind == "tool.completed":
            call_id = str(data.get("tool_call_id") or "")
            name = str(data.get("name") or "tool")
            args = self._tool_args.pop(call_id, {})
            if not args and isinstance(data.get("arguments"), dict):
                args = data["arguments"]
            success = bool(data.get("success"))
            duration = float(data.get("duration_ms") or 0)
            cached = bool(data.get("cached"))
            with self._lock:
                self._emit_tool_locked(name, args, success, duration, cached)
            self._set_status("Thinking", f"after {name}")
            self._resume()
        elif kind == "llm.empty":
            if data.get("retry"):
                line = Text("  ~ ", style="yellow")
                line.append(
                    "empty reply"
                    + (
                        " (output limit)"
                        if data.get("finish_reason") == "length"
                        else ""
                    )
                    + ", retrying",
                    style="dim",
                )
                with self._lock:
                    self._note_locked(line)
                self._resume()
        elif kind in {"context.trimmed", "context.compressed"}:
            if kind == "context.trimmed":
                line = Text("  ", style="dim")
                line.append("~ ", style="dim")
                line.append(f"trimmed {int(data.get('count') or 0)} old tool result(s)")
                line.stylize("dim")
            else:
                line = Text(
                    f"  ~ context compressed ({int(data.get('tokens_estimate') or 0):,} tok)",
                    style="dim",
                )
            with self._lock:
                self._note_locked(line)
            self._resume()
        elif kind == "memory.retrieved":
            line = Text("  * memory ", style="magenta")
            line.append(f"{int(data.get('count') or 0)} hit(s)", style="dim")
            with self._lock:
                self._note_locked(line)
            self._resume()
        elif kind == "memory.written":
            line = Text("  * remembered ", style="magenta")
            line.append(f"{int(data.get('count') or 0)} item(s)", style="dim")
            with self._lock:
                self._note_locked(line)
            self._resume()
        elif kind == "autorun.completed":
            passed = bool(data.get("passed"))
            line = Text(f"  {'ok' if passed else 'FAIL':<4} auto-verify ", style="green" if passed else "bold red")
            line.append(str(data.get("command", "")), style="dim")
            with self._lock:
                self._note_locked(line)
            self._resume()

    # ---- lifecycle ---------------------------------------------------
    def begin(self) -> None:
        with self._lock:
            self._finished = False
            self._started = time.perf_counter()
            self._run_tokens = 0
            self._round = 0
            self._reasoning_total = 0
            self._reasoning_printed = 0
            self._reasoning_hidden = False
            self._streamed_any = False
            self._md_buffer = ""
            self._reasoning_buffer = ""
            self._reasoning_line_open = False
            self._last_kind = None
            self._prev_trailing_blank = False
            self._set_status("Thinking", "")
            self._start_live()

    def finish(self, result=None) -> bool:
        """Stop rendering and print the turn receipt. Returns True if text streamed."""

        with self._lock:
            self._close_reasoning_locked()
            self._flush_markdown_locked(force=True)
            trailing_blank = self._last_kind == "content" and self._prev_trailing_blank
            if self._last_kind is not None and not trailing_blank:
                self.console.print()
            self._last_kind = None
            self._finished = True
            self._stop_live()
            streamed = self._streamed_any
        stats = getattr(result, "stats", None)
        if stats is not None:
            try:
                from .metrics import TurnReceipt

                receipt = TurnReceipt(
                    run_id=stats.run_id,
                    session_id=getattr(stats, "session_id", ""),
                    model=getattr(stats, "model", ""),
                    prompt_tokens=stats.prompt_tokens,
                    completion_tokens=stats.completion_tokens,
                    cost_usd=stats.cost_usd,
                    rounds=stats.rounds,
                    tool_calls=stats.tool_calls,
                    tool_failures=stats.tool_failures,
                    cache_reuses=getattr(stats, "cache_reuses", 0),
                    trimmed_results=getattr(stats, "trimmed_results", 0),
                    elapsed_ms=stats.elapsed_ms,
                    stop_reason=stats.stop_reason,
                )
                self.console.print(f"[dim]{receipt.line()}[/dim]", highlight=False)
            except Exception:
                pass
            if stats.stop_reason not in {"completed", ""}:
                self.console.print(f"[yellow]Stopped: {stats.stop_reason}[/yellow]")
        return streamed

    def pause_for_input(self) -> None:
        """Release the terminal before an interactive tool prompt."""

        self._pause()

    def resume_after_input(self) -> None:
        self._resume()

    def note(self, text: str) -> None:
        with self._lock:
            self._note_locked(Text(f"  {text}", style="dim"))
        self._resume()

    def print(self, *args, **kwargs) -> None:
        self._pause()
        self.console.print(*args, **kwargs)
        self._resume()


# Backwards-friendly name used by the CLI and tests.
LiveRenderer = _TerminalRenderer
