"""Search-and-replace file editing (Claude Code's key innovation).

The core idea: instead of sending whole-file rewrites or line-number patches,
the LLM specifies an *exact* substring to find and its replacement. The
substring must appear exactly once in the file, which eliminates ambiguity
and makes edits safe and reviewable.

Two practical additions keep the loop out of dead ends on real repositories:
line endings are normalised for matching (Windows checkouts use CRLF), and a
trailing-whitespace-tolerant match is attempted when the exact text is absent.
``multi_edit`` applies several replacements in one round instead of one replay
of the whole conversation per edit.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from ..fsutil import atomic_write_text
from .base import WorkspaceTool


@dataclass
class FileChange:
    path: str
    before_sha256: str | None
    after_sha256: str
    before_content: str | None = None
    after_content: str = ""


class ChangeTracker:
    """Per-agent mutation journal used by status, sessions, and /diff."""

    def __init__(self) -> None:
        self._changes: dict[str, FileChange] = {}
        self._lock = threading.RLock()

    def record(self, path: Path, before: str | None, after: str) -> None:
        def digest(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        with self._lock:
            existing = self._changes.get(str(path))
            before_hash = (
                existing.before_sha256
                if existing
                else (digest(before) if before is not None else None)
            )
            before_content = existing.before_content if existing else before
            self._changes[str(path)] = FileChange(
                str(path),
                before_hash,
                digest(after),
                before_content,
                after,
            )

    def paths(self) -> list[str]:
        with self._lock:
            return sorted(self._changes)

    def snapshot(self, *, include_content: bool = False) -> list[dict]:
        with self._lock:
            items = [vars(self._changes[path]).copy() for path in sorted(self._changes)]
        if include_content:
            return items
        for item in items:
            item.pop("before_content", None)
            item.pop("after_content", None)
        return items

    def restore_all(self) -> list[str]:
        """Restore every tracked file to its pre-run content (best effort)."""

        with self._lock:
            changes = [self._changes[path] for path in sorted(self._changes)]
            self._changes.clear()
        restored: list[str] = []
        for change in changes:
            path = Path(change.path)
            try:
                if change.before_content is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write_text(path, change.before_content)
                restored.append(change.path)
            except OSError:
                continue
        return restored

    def clear(self) -> None:
        with self._lock:
            self._changes.clear()


_DEFAULT_TRACKER = ChangeTracker()
# Backward-compatible view used by older integrations and tests.
_changed_files: set[str] = set()


def _reader(path: Path) -> str:
    # newline="" preserves CRLF; universal-newline translation here would
    # silently rewrite every Windows file to LF on the next edit.
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()


def _write(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def _line_numbers(content: str, needle: str, limit: int = 5) -> str:
    """Human-readable locations of an ambiguous match."""

    lines: list[str] = []
    start = 0
    while len(lines) < limit:
        index = content.find(needle, start)
        if index < 0:
            break
        number = content.count("\n", 0, index) + 1
        lines.append(str(number))
        start = index + max(1, len(needle))
    return ", ".join(lines)


def _nearest_block(content: str, needle: str) -> str:
    """A short diagnostic when the exact text is absent."""

    first = next((line.strip() for line in needle.splitlines() if line.strip()), needle)
    if not first:
        return ""
    candidates = [line for line in content.splitlines() if line.strip()]
    close = difflib.get_close_matches(first, candidates, n=2, cutoff=0.55)
    if not close:
        return ""
    return "Nearest lines: " + " | ".join(item[:120] for item in close)


def _tolerant_pattern(needle: str) -> re.Pattern | None:
    """Match the same lines while ignoring trailing whitespace and CRLF."""

    lines = needle.splitlines()
    if not lines or not any(line.strip() for line in lines):
        return None
    escaped = [re.escape(line.rstrip()) + r"[ \t]*" for line in lines]
    try:
        return re.compile(r"(?:\r?\n)".join(escaped))
    except re.error:
        return None


@dataclass
class _Match:
    count: int
    tolerant: bool = False
    start: int = -1
    end: int = -1


def _find(content: str, needle: str) -> _Match:
    count = content.count(needle) if needle else 0
    if count:
        return _Match(count=count)
    pattern = _tolerant_pattern(needle)
    if pattern is None:
        return _Match(count=0)
    matches = list(pattern.finditer(content))
    if len(matches) == 1:
        return _Match(count=1, tolerant=True, start=matches[0].start(), end=matches[0].end())
    return _Match(count=len(matches), tolerant=True)


def _newline_style(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _apply_edit(
    content: str, old_string: str, new_string: str, replace_all: bool
) -> tuple[str | None, str, str | None]:
    """Apply one replacement.

    Returns ``(new_content, message, error)``.  ``new_content`` is None when
    nothing was applied.
    """

    if not old_string:
        return None, "", "old_string must not be empty"
    if old_string == new_string:
        return None, "old_string and new_string are identical; nothing to do.", None

    match = _find(content, old_string)
    if match.count == 0:
        hint = _nearest_block(content, old_string)
        error = f"old_string not found in the file. {hint}".strip()
        return None, "", error
    if match.count > 1 and not replace_all:
        where = _line_numbers(content, old_string)
        suffix = f" (lines {where})" if where else ""
        return (
            None,
            "",
            f"old_string appears {match.count} times{suffix}. Include more "
            "surrounding lines to make it unique, or set replace_all=true.",
        )

    style = _newline_style(content)
    if match.tolerant:
        # The tolerant regex matched across the original line endings.  Build
        # the replacement in the file's own newline style.
        replacement = new_string
        if style == "\r\n":
            replacement = new_string.replace("\r\n", "\n").replace("\n", "\r\n")
        pattern = _tolerant_pattern(old_string)
        if replace_all and pattern is not None:
            new_content, replaced = pattern.subn(lambda _m: replacement, content)
            note = f" ({replaced} occurrences replaced, tolerant match)"
        else:
            new_content = content[: match.start] + replacement + content[match.end :]
            note = " (matched while ignoring trailing whitespace/line endings)"
    else:
        replacement = new_string
        if style == "\r\n" and "\r\n" not in old_string and "\r\n" not in new_string:
            # The model read LF text through read_file's normalisation but the
            # file uses CRLF; keep the file's convention.
            replacement = new_string.replace("\n", "\r\n")
        count = -1 if replace_all else 1
        new_content = content.replace(old_string, replacement, count)
        note = f" ({match.count} occurrences replaced)" if replace_all else ""
    return new_content, f"Edited the file{note}", None


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """Generate a compact unified diff between old and new file content."""

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    if len(result) > 3000:
        result = result[:2500] + "\n... (diff truncated)\n"
    return result


class EditFileTool(WorkspaceTool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match. "
        "old_string must appear exactly once in the file for safety. "
        "Include enough surrounding context to ensure uniqueness. "
        "Set replace_all=true to replace every occurrence."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique unless replace_all)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring uniqueness",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    }
    risk = "write"
    parallel_safe = False
    timeout_s = 30.0

    def __init__(self, workspace, tracker: ChangeTracker | None = None):
        super().__init__(workspace)
        self.tracker = tracker or _DEFAULT_TRACKER

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        try:
            path = self.workspace.resolve(file_path)
            if not path.exists():
                return f"Error: {file_path} not found"
            if path.is_dir():
                return f"Error: {file_path} is a directory"

            content = _reader(path)
            new_content, message, error = _apply_edit(
                content, old_string, new_string, bool(replace_all)
            )
            if error:
                preview = content[:500] + ("..." if len(content) > 500 else "")
                return f"Error: {error}\nFile starts with:\n{preview}"
            if new_content is None:
                return message or "No change applied."
            if new_content == content:
                return "No change applied (content is identical)."

            _write(path, new_content)
            self.tracker.record(path, content, new_content)
            _changed_files.add(str(path))
            diff = _unified_diff(content, new_content, str(path))
            return f"{message} {file_path}\n{diff}"
        except Exception as exc:
            return f"Error: {exc}"


class MultiEditTool(WorkspaceTool):
    name = "multi_edit"
    description = (
        "Apply several search-and-replace edits to one file in a single call. "
        "Each edit follows edit_file's rules; edits are applied in order. "
        "Prefer this over repeated edit_file calls when changing several places."
    )
    risk = "write"
    parallel_safe = False
    timeout_s = 45.0
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit"},
            "edits": {
                "type": "array",
                "description": "Ordered list of edits",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["old_string", "new_string"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["file_path", "edits"],
        "additionalProperties": False,
    }

    def __init__(self, workspace, tracker: ChangeTracker | None = None):
        super().__init__(workspace)
        self.tracker = tracker or _DEFAULT_TRACKER

    def execute(self, file_path: str, edits: list[dict]) -> str:
        try:
            path = self.workspace.resolve(file_path)
            if not path.exists():
                return f"Error: {file_path} not found"
            if not isinstance(edits, list) or not edits:
                return "Error: edits must be a non-empty array"
            original = _reader(path)
            content = original
            messages: list[str] = []
            for index, edit in enumerate(edits, 1):
                if not isinstance(edit, dict):
                    return f"Error: edit {index} must be an object"
                old = edit.get("old_string", "")
                new = edit.get("new_string", "")
                updated, message, error = _apply_edit(
                    content, old, new, bool(edit.get("replace_all", False))
                )
                if error:
                    return (
                        f"Error: edit {index} failed; no changes written: {error}"
                    )
                if updated is not None:
                    content = updated
                    messages.append(f"{index}. {message}")
            if content == original:
                return "No change applied (content is identical)."
            _write(path, content)
            self.tracker.record(path, original, content)
            _changed_files.add(str(path))
            diff = _unified_diff(original, content, str(path))
            return (
                f"Applied {len(messages)} edit(s) to {file_path}\n"
                + "\n".join(messages)
                + "\n"
                + diff
            )
        except Exception as exc:
            return f"Error: {exc}"
