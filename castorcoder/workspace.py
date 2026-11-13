"""Workspace boundary, project instructions, and path policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when a tool attempts to leave the configured workspace."""


@dataclass
class Workspace:
    """Resolve all tool paths against a stable project root.

    ``allow_outside`` is an explicit escape hatch for advanced users.  The
    default keeps autonomous file operations within the project that launched
    the agent.
    """

    root: Path | str = field(default_factory=Path.cwd)
    allow_outside: bool = False

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.exists():
            raise ValueError(f"Workspace does not exist: {self.root}")
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.root}")

    def resolve(self, value: str | Path = ".", *, must_exist: bool = False) -> Path:
        raw = Path(value).expanduser()
        candidate = (raw if raw.is_absolute() else self.root / raw).resolve()
        if not self.allow_outside and not self.contains(candidate):
            raise WorkspaceViolation(
                f"Path is outside workspace: {candidate} (root: {self.root})"
            )
        if must_exist and not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate

    def contains(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def relative(self, path: str | Path) -> str:
        candidate = Path(path).resolve()
        try:
            return str(candidate.relative_to(self.root))
        except ValueError:
            return str(candidate)

    def project_instructions(self, max_chars: int = 16_000) -> str:
        """Load repository guidance without recursively scanning the tree."""

        candidates = [
            self.root / "AGENTS.md",
            self.root / "CLAUDE.md",
            self.root / ".castorcoder" / "instructions.md",
        ]
        parts: list[str] = []
        remaining = max_chars
        for path in candidates:
            if remaining <= 0 or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            text = text[:remaining]
            parts.append(f"## {path.name}\n{text}")
            remaining -= len(text)
        return "\n\n".join(parts)

    @property
    def shell(self) -> str:
        return "powershell" if os.name == "nt" else "sh"
