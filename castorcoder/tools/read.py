"""File and directory reading with line numbers and structure view."""

from __future__ import annotations

import os
from pathlib import Path

from .base import WorkspaceTool

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".tmp", ".castorcoder",
}
_TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg",
    ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".scss", ".sh", ".rs",
    ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".sql", ".xml", ".env",
}
_MAX_READ_BYTES = 8_000_000
_MAX_LIST_FILE_BYTES = 300_000


class ReadFileTool(WorkspaceTool):
    name = "read_file"
    description = (
        "Read a file's contents with line numbers, or list directory contents if given a folder path. "
        "Always read a file before editing it. Use offset/limit for large files."
    )
    timeout_s = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file or directory to inspect",
            },
            "offset": {
                "type": "integer",
                "description": "Start line (1-based). Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read. Default 2000.",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def execute(self, file_path: str, offset: int = 1, limit: int = 2000) -> str:
        try:
            path = self.workspace.resolve(file_path or ".")
            if not path.exists():
                suggestion = self._suggest_close_matches(file_path)
                message = f"Error: {file_path} not found"
                if suggestion:
                    message += f". Did you mean: {suggestion}?"
                return message

            if path.is_dir():
                return self._list_dir(path)

            size = path.stat().st_size
            if size > _MAX_READ_BYTES:
                return (
                    f"Error: {file_path} is {size / 1_048_576:.1f} MB, too large to read "
                    "at once. Use grep with a path, or read a smaller file."
                )
            with path.open("rb") as handle:
                probe = handle.read(8_192)
            if b"\x00" in probe:
                return f"Error: {file_path} appears to be a binary file ({size:,} bytes)."

            start = max(0, int(offset) - 1)
            limit = max(1, min(int(limit), 20_000))
            lines: list[str] = []
            total = 0
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for total, line in enumerate(handle, 1):
                    if total <= start:
                        continue
                    if len(lines) >= limit:
                        continue
                    lines.append(line.rstrip("\n").rstrip("\r"))
                # ``total`` is the last line number seen; keep counting if the
                # file is longer than the requested window.
                if len(lines) >= limit:
                    total += sum(1 for _ in handle)

            numbered = [f"{start + index + 1}\t{line}" for index, line in enumerate(lines)]
            result = "\n".join(numbered)
            if total > start + len(lines):
                result += (
                    f"\n... ({total} lines total, showing {start + 1}-{start + len(lines)})"
                )
            return result or "(empty file)"
        except Exception as exc:
            return f"Error: {exc}"

    def _suggest_close_matches(self, file_path: str) -> str:
        try:
            target_name = Path(file_path).name.lower()
            if not target_name:
                return ""
            matches: list[str] = []
            for dirpath, dirnames, filenames in os.walk(self.workspace.root):
                dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
                for name in filenames:
                    if target_name in name.lower():
                        matches.append(self.workspace.relative(Path(dirpath) / name))
                        if len(matches) >= 3:
                            return ", ".join(matches)
            return ", ".join(matches)
        except Exception:
            return ""

    def _list_dir(self, directory: Path) -> str:
        entries: list[str] = []
        try:
            relative = directory.relative_to(self.workspace.root).as_posix()
            display_name = relative if relative != "." else "(workspace root)"
        except ValueError:
            display_name = str(directory)

        try:
            with os.scandir(directory) as iterator:
                items = sorted(
                    iterator,
                    key=lambda item: (not item.is_dir(follow_symlinks=False), item.name.lower()),
                )
            for item in items:
                if item.name in _SKIP_DIRS:
                    continue
                if item.is_dir(follow_symlinks=False):
                    entries.append(f"  [DIR]  {item.name}/")
                    continue
                try:
                    size = item.stat(follow_symlinks=False).st_size
                except OSError:
                    entries.append(f"  [FILE] {item.name}")
                    continue
                size_text = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
                suffix = Path(item.name).suffix.lower()
                if size < _MAX_LIST_FILE_BYTES and suffix in _TEXT_SUFFIXES:
                    try:
                        with open(item.path, "rb") as handle:
                            sample = handle.read(8_192)
                        if b"\x00" in sample:
                            raise ValueError("binary")
                        with open(item.path, encoding="utf-8", errors="ignore") as text_file:
                            line_count = sum(1 for _ in text_file)
                        entries.append(
                            f"  [FILE] {item.name} ({line_count} lines, {size_text})"
                        )
                        continue
                    except (OSError, ValueError):
                        pass
                entries.append(f"  [FILE] {item.name} ({size_text})")
        except Exception as exc:
            return f"Error listing directory {display_name}: {exc}"

        header = f"Directory: {display_name} ({len(entries)} items)\n"
        if not entries:
            return f"Directory: {display_name} (empty)"
        if len(entries) > 300:
            entries = entries[:300] + [f"  ... ({len(entries) - 300} more; use glob to filter)"]
        return header + "\n".join(entries)
