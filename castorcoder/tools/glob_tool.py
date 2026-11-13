"""File pattern matching with directory pruning."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path, PurePosixPath

from .base import WorkspaceTool

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".tmp", ".castorcoder",
}


class GlobTool(WorkspaceTool):
    name = "glob"
    description = (
        "Find files matching a glob pattern. "
        "Supports ** for recursive matching (e.g. '**/*.py')."
    )
    timeout_s = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: cwd)",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def execute(self, pattern: str, path: str = ".") -> str:
        try:
            base = self.workspace.resolve(path)
            if not base.is_dir():
                return f"Error: {path} is not a directory"

            hits: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name not in _SKIP_DIRS
                    and not os.path.islink(os.path.join(dirpath, name))
                )
                for name in filenames:
                    full = Path(dirpath) / name
                    if os.path.islink(full):
                        continue
                    relative = full.relative_to(base).as_posix()
                    if self._matches(relative, name, pattern):
                        hits.append(full)
                        if len(hits) >= 5_000:
                            break
                if len(hits) >= 5_000:
                    break

            hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            total = len(hits)
            shown = hits[:100]
            lines = [self.workspace.relative(hit) for hit in shown]
            result = "\n".join(lines)
            if total > 100:
                result += f"\n... ({total} matches, showing first 100)"
            return result or "No files matched."
        except Exception as exc:
            return f"Error: {exc}"

    @staticmethod
    def _matches(relative: str, name: str, pattern: str) -> bool:
        if "/" not in pattern and "**" not in pattern:
            return fnmatch.fnmatch(name, pattern)
        path = PurePosixPath(relative)
        try:
            if path.match(pattern):
                return True
            # ``**/`` should also match a top-level file (pathlib's matcher
            # requires one directory component in some Python versions).
            if pattern.startswith("**/"):
                return path.match(pattern[3:])
            return False
        except ValueError:
            return fnmatch.fnmatch(relative, pattern)
