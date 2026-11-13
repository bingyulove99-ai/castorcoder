"""Content search with regex support.

The original implementation walked every file with ``Path.rglob`` and read
each one in full, including binaries and virtualenvs.  That made a simple
search the slowest part of many turns.  This version prunes directories while
walking, skips binaries and oversized files, optionally uses ``ripgrep`` when
it is installed, and bounds the result set.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from .base import WorkspaceTool

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".tmp", ".castorcoder", "pytest-cache-files-*",
}
# Broad recursive searches skip common credential files.  A caller can still
# name one explicitly as ``path`` when that is the intent.
_SKIP_FILES = {".env", ".netrc", ".pgpass", "credentials", "secrets", ".castorcoder.toml"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
_MAX_FILE_BYTES = 2_000_000
_BINARY_PROBE = 8_192


class GrepTool(WorkspaceTool):
    name = "grep"
    description = (
        "Search file contents with a regular expression. Returns matching lines "
        "with file path and line number. Skips binaries and dependency folders."
    )
    timeout_s = 60.0
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory (default: cwd)"},
            "include": {
                "type": "string",
                "description": "Only search files matching this glob (e.g. '*.py')",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matching lines to return (default 200)",
            },
            "context": {
                "type": "integer",
                "description": "Lines of context around each match (default 0)",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive matching (default true)",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def execute(
        self,
        pattern: str,
        path: str = ".",
        include: str | None = None,
        max_results: int = 200,
        context: int = 0,
        case_sensitive: bool = True,
    ) -> str:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return f"Invalid regex: {exc}"

        try:
            base = self.workspace.resolve(path)
        except Exception as exc:
            return f"Error: {exc}"
        if not base.exists():
            return f"Error: {path} not found"

        limit = max(1, min(int(max_results), 1000))
        context = max(0, min(int(context), 10))

        if shutil.which("rg") and base.is_dir():
            fast = self._ripgrep(pattern, base, include, limit, case_sensitive, context)
            if fast is not None:
                return fast

        files = [base] if base.is_file() else list(self._walk(base, include))
        matches: list[str] = []
        seen = 0
        for file_path in files:
            try:
                raw = file_path.read_bytes()
            except OSError:
                continue
            if len(raw) > _MAX_FILE_BYTES or b"\x00" in raw[:_BINARY_PROBE]:
                continue
            text = raw.decode("utf-8", "replace")
            lines = text.splitlines()
            rel = self.workspace.relative(file_path)
            for lineno, line in enumerate(lines, 1):
                if not regex.search(line):
                    continue
                if context:
                    start = max(0, lineno - context - 1)
                    end = min(len(lines), lineno + context)
                    for index in range(start, end):
                        marker = ":" if index == lineno - 1 else "-"
                        matches.append(f"{rel}{marker}{index + 1}{marker} {lines[index].rstrip()}")
                else:
                    matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                seen += 1
                if seen >= limit:
                    matches.append(f"... ({limit} match limit reached)")
                    return "\n".join(matches)

        return "\n".join(matches) if matches else "No matches found."

    def _ripgrep(
        self,
        pattern: str,
        base: Path,
        include: str | None,
        limit: int,
        case_sensitive: bool,
        context: int,
    ) -> str | None:
        command = [shutil.which("rg") or "rg", "--line-number", "--no-heading",
                   "--color", "never", "--max-columns", "500"]
        if not case_sensitive:
            command.append("-i")
        if context:
            command.extend(["-C", str(context)])
        if include:
            command.extend(["--glob", include])
        for directory in sorted(_SKIP_DIRS):
            command.extend(["--glob", f"!{directory}/**"])
        for name in sorted(_SKIP_FILES):
            command.extend(["--glob", f"!{name}"])
        for suffix in sorted(_SECRET_SUFFIXES):
            command.extend(["--glob", f"!*{suffix}"])
        try:
            target = self.workspace.relative(base) or "."
        except Exception:
            target = "."
        command.extend(["-e", pattern, "--", target])
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.workspace.root),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout_s,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = proc.stdout.splitlines()
        if len(lines) > limit:
            lines = lines[:limit] + [f"... ({limit} match limit reached)"]
        return "\n".join(lines) if lines else "No matches found."

    @staticmethod
    def _walk(root: Path, include: str | None):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in _SKIP_DIRS
                and not any(fnmatch.fnmatch(name, pattern) for pattern in _SKIP_DIRS if "*" in pattern)
                and not os.path.islink(os.path.join(dirpath, name))
            )
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if os.path.islink(full):
                    continue
                lower_name = name.lower()
                if (
                    lower_name in _SKIP_FILES
                    or lower_name.startswith(".env.")
                    or full.suffix.lower() in _SECRET_SUFFIXES
                ):
                    continue
                if include:
                    relative = full.relative_to(root).as_posix()
                    if not (
                        fnmatch.fnmatch(name, include)
                        or fnmatch.fnmatch(relative, include)
                        or fnmatch.fnmatch(relative, f"**/{include}")
                    ):
                        continue
                yield full
