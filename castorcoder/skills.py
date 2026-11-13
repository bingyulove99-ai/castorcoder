"""Progressive-disclosure skill discovery and loading.

Only names and descriptions enter the system prompt.  Full instructions are
loaded on demand through ``load_skill`` so large skill libraries do not consume
the model context on every request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tools.base import Tool
from .workspace import Workspace


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


class SkillCatalog:
    def __init__(self, workspace: Workspace, extra_dirs: list[str | Path] | None = None):
        self.workspace = workspace
        self.extra_dirs = [Path(p).expanduser() for p in (extra_dirs or [])]
        self._skills: dict[str, Skill] = {}
        self.refresh()

    @property
    def search_dirs(self) -> list[Path]:
        candidates = [
            self.workspace.root / ".castorcoder" / "skills",
            self.workspace.root / ".claude" / "skills",
            Path.home() / ".castorcoder" / "skills",
            *self.extra_dirs,
        ]
        seen: set[Path] = set()
        result: list[Path] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        return result

    def refresh(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for directory in self.search_dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*/SKILL.md")):
                skill = self._metadata(path)
                # Project-local skills win because search_dirs is ordered.
                found.setdefault(skill.name, skill)
            for path in sorted(directory.glob("*.md")):
                skill = self._metadata(path)
                found.setdefault(skill.name, skill)
        self._skills = found
        return self.list()

    def list(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda item: item.name.lower())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name) or next(
            (skill for key, skill in self._skills.items() if key.lower() == name.lower()),
            None,
        )

    def load(self, name: str, max_chars: int = 24_000) -> str:
        skill = self.get(name)
        if skill is None:
            available = ", ".join(item.name for item in self.list()) or "none"
            raise ValueError(f"Unknown skill '{name}'. Available: {available}")
        text = skill.path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n... (skill truncated)"
        return text

    def prompt_catalog(self) -> str:
        skills = self.list()
        if not skills:
            return "(no skills discovered)"
        return "\n".join(f"- {item.name}: {item.description}" for item in skills)

    @staticmethod
    def _metadata(path: Path) -> Skill:
        text = path.read_text(encoding="utf-8", errors="replace")[:8_000]
        name = path.parent.name if path.name == "SKILL.md" else path.stem
        description = "Reusable project workflow."

        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                frontmatter = text[3:end]
                for line in frontmatter.splitlines():
                    key, sep, value = line.partition(":")
                    if not sep:
                        continue
                    value = value.strip().strip("'\"")
                    if key.strip() == "name" and value:
                        name = value
                    elif key.strip() == "description" and value:
                        description = value

        if description == "Reusable project workflow.":
            for line in text.splitlines():
                clean = line.strip()
                if clean and not clean.startswith(("#", "---", "name:", "description:")):
                    description = re.sub(r"\s+", " ", clean)[:240]
                    break

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
        return Skill(safe_name or path.stem, description[:240], path.resolve())


class SkillTool(Tool):
    name = "load_skill"
    description = (
        "List reusable skills or load one skill's full instructions. "
        "Use action=list first when the relevant skill name is unknown."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 15.0
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "load", "refresh"]},
            "name": {"type": "string", "description": "Skill name for action=load"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, catalog: SkillCatalog):
        self.catalog = catalog

    def execute(self, action: str, name: str | None = None) -> str:
        if action == "list":
            return self.catalog.prompt_catalog()
        if action == "refresh":
            skills = self.catalog.refresh()
            return f"Discovered {len(skills)} skills.\n{self.catalog.prompt_catalog()}"
        if action == "load":
            if not name:
                return "Error: action=load requires name"
            try:
                return f"[Skill: {name}]\n{self.catalog.load(name)}"
            except (OSError, ValueError) as exc:
                return f"Error: {exc}"
        return f"Error: unknown action '{action}'"
