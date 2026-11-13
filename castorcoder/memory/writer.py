"""Deterministic post-run memory distillation.

The writer is intentionally heuristic: it runs offline, is cheap, and never
needs a second model call.  An LLM summarizer can be layered on top later, but
the baseline must remain testable without network access.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .store import MemoryStore

if TYPE_CHECKING:
    from ..agent import AgentStats

_ERROR_PREFIXES = ("error:", "blocked:", "sub-agent error:")
_DECISION_MARKERS = (
    "decided",
    "chose",
    "will change",
    "root cause",
    "fix is",
    "决定",
    "采用",
    "根因",
    "改为",
)
_PREFERENCE_MARKERS = (
    "always",
    "never",
    "prefer",
    "must not",
    "不要",
    "必须",
    "总是",
    "以后",
    "统一",
)
MAX_ERRORS = 5
MAX_DECISIONS = 3
MAX_PREFERENCES = 3


class MemoryWriter:
    """Extract durable, deduplicated memories from one completed run."""

    def __init__(self, store: MemoryStore, *, min_tool_calls: int = 1) -> None:
        self.store = store
        self.min_tool_calls = min_tool_calls

    def distill(
        self,
        messages: list[dict],
        stats: AgentStats,
        *,
        prompt: str = "",
        changed_paths: list[str] | None = None,
    ) -> list[str]:
        changed = list(changed_paths or [])
        if stats.tool_calls < self.min_tool_calls and not changed:
            return []

        written: list[str] = []
        episode = self._episode(stats, changed)
        written.extend(self._remember(
            episode, kind="episode", source=stats.run_id, confidence=0.6, ttl_days=180
        ))

        for line in self._errors(messages):
            written.extend(self._remember(line, kind="error", source=stats.run_id,
                                          confidence=0.7))

        for line in self._preferences(messages, prompt):
            written.extend(self._remember(line, kind="preference", source=stats.run_id,
                                          confidence=0.8))

        for line in self._decisions(messages):
            written.extend(self._remember(line, kind="decision", source=stats.run_id,
                                          confidence=0.55))
        return written

    def _remember(
        self,
        content: str,
        *,
        kind: str,
        source: str,
        confidence: float,
        ttl_days: float | None = None,
    ) -> list[str]:
        from .gate import AntiPatternFilter

        if not AntiPatternFilter.is_acceptable(content):
            return []
        try:
            record, created = self.store.add(
                content,
                kind=kind,
                source=source,
                confidence=confidence,
                ttl_days=ttl_days,
            )
        except (ValueError, OSError):
            return []
        return [record.id] if created else []

    @staticmethod
    def _episode(stats: AgentStats, changed: list[str]) -> str:
        files = ", ".join(changed[:12]) if changed else "no file changes"
        return (
            f"Run {stats.run_id} stopped with '{stats.stop_reason}' after "
            f"{stats.rounds} rounds and {stats.tool_calls} tool calls; {files}."
        )

    @staticmethod
    def _errors(messages: list[dict]) -> list[str]:
        found: list[str] = []
        for message in messages:
            if message.get("role") != "tool":
                continue
            text = str(message.get("content") or "")
            for line in text.splitlines():
                clean = line.strip()
                if clean.lower().startswith(_ERROR_PREFIXES):
                    clean = re.sub(r"\s+", " ", clean)[:400]
                    if clean not in found:
                        found.append(clean)
                if len(found) >= MAX_ERRORS:
                    return found
        return found

    @staticmethod
    def _decisions(messages: list[dict]) -> list[str]:
        found: list[str] = []
        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            text = str(message.get("content") or "")
            for line in text.splitlines():
                clean = re.sub(r"\s+", " ", line.strip().lstrip("-*0123456789. "))
                if len(clean) < 8 or not any(marker in clean.lower() for marker in _DECISION_MARKERS):
                    continue
                if clean not in found:
                    found.append(clean[:400])
                if len(found) >= MAX_DECISIONS:
                    return found
            if found:
                break
        return found

    @staticmethod
    def _preferences(messages: list[dict], prompt: str) -> list[str]:
        found: list[str] = []
        texts = [prompt] + [
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "user"
        ]
        for text in texts:
            for line in text.splitlines():
                lowered = line.lower()
                if len(line.strip()) < 6 or not any(
                    marker in lowered for marker in _PREFERENCE_MARKERS
                ):
                    continue
                clean = re.sub(r"\s+", " ", line.strip())[:400]
                if clean not in found:
                    found.append(clean)
                if len(found) >= MAX_PREFERENCES:
                    return found
        return found
