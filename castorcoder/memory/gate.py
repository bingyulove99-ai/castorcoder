"""Smart memory lifecycle controls: retrieval gate, slot gate, and export.

Inspired by waku-agent's memory pillars:
- Retrieval Gate: Decides whether a turn even needs long-term memory, avoiding
  latency and context pollution ("over-interpretation").
- Slot Gate: Evaluates retrieved memories and selects only those that earn a slot
  in the prompt context.
- Anti-Pattern Filter: Drops transient operational states, meta-memory claims,
  and direct restatements of recalled knowledge.
- Markdown Mirror: Exports durable memories to human-readable MEMORY.md.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .models import MEMORY_KINDS, MemoryHit, MemoryRecord

if TYPE_CHECKING:
    from ..llm import LLM
    from .store import MemoryStore

logger = logging.getLogger(__name__)

# Heuristic patterns that indicate memory is relevant
_MEMORY_TRIGGER_KEYWORDS = (
    "remember",
    "recall",
    "preference",
    "convention",
    "decision",
    "last time",
    "rule",
    "guideline",
    "style",
    "standard",
    "history",
    "惯例",
    "偏好",
    "决定",
    "上次",
    "规约",
    "规范",
    "历史",
    "约定",
    "规范",
    "项目结构",
)

# Transient ops patterns that must never be recorded into durable memory
_OPS_STATE_PATTERNS = (
    re.compile(r"\b(401|402|403|404|429|500|502|503)\b"),
    re.compile(r"\b(rate limit|token limit|quota|balance|spend|credit)\b", re.I),
    re.compile(r"\b(connection\s*(error|reset|refused|timed?\s*out))\b", re.I),
    re.compile(r"\b(transient|temporary)\s*failure\b", re.I),
    re.compile(r"\b(api[-_ ]?key|secret|password|bearer)\b", re.I),
    re.compile(r"额度|余额|超时|网络错误|请求超限", re.I),
)

_META_MEMORY_PATTERNS = (
    re.compile(r"no\s+(memory|record|convention|fact)\s+(found|exists|stored)", re.I),
    re.compile(r"(memory|store)\s+(lacks|does not have|has no)", re.I),
    re.compile(r"未找到相关(记忆|记录|规约|信息)", re.I),
)

GATE_PROMPT = """\
You are a retrieval gate for an AI coding agent's long-term memory.
Given the user's task or message, decide if fulfilling it requires recalling historical
project conventions, architectural decisions, coding preferences, or known error patterns.

Reply with ONLY this JSON, nothing else:
{{"retrieve": true/false, "query": "<search keywords if true, else empty>", "reason": "<5 words summary>"}}

Guidelines:
- Self-contained code tasks, local refactorings, syntax questions, or simple math -> retrieve: false
- Questions about project architecture, team conventions, past bugfixes, or user preferences -> retrieve: true

User message:
{message}"""


class RetrievalGate:
    """Decides whether to hit the memory store before round 1."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def should_retrieve(
        self,
        llm: LLM | None,
        message: str,
    ) -> tuple[bool, str, str]:
        """Returns (retrieve?, query, reason). Fails open: returns True on error."""
        if not self.enabled:
            return True, message, "gate disabled"

        clean = message.strip()
        if not clean:
            return False, "", "empty message"

        # Fast heuristic check: if keywords directly match, trigger retrieve immediately
        lowered = clean.lower()
        for kw in _MEMORY_TRIGGER_KEYWORDS:
            if kw in lowered:
                return True, clean, f"keyword match '{kw}'"

        # If no LLM available (e.g. offline tests), use heuristic
        if llm is None:
            # Self-contained action verbs
            self_contained = re.match(
                r"^(读|看|查|写|修改|编辑|修复|优化|测试|实现|分析|解释|运行|搜索|查看|列出|总结|检|rename|delete|remove|format|add|create|check|print|test|run|show|read|inspect|fix|view|list|find|grep|cat|diff)\b",
                lowered,
            )
            if self_contained and len(clean.splitlines()) <= 2:
                return False, "", "heuristic: self-contained command"
            return True, clean, "heuristic default"

        try:
            prompt = GATE_PROMPT.format(message=clean[:1000])
            resp = llm.chat([{"role": "user", "content": prompt}], tools=[])
            text = (resp.content or "").strip()
            match = re.search(r"\{.*?\}", text, re.DOTALL)
            if not match:
                return True, clean, "gate returned non-JSON (fail open)"
            data = json.loads(match.group(0))
            need_retrieve = bool(data.get("retrieve", True))
            query = str(data.get("query") or clean)
            reason = str(data.get("reason", "model verdict"))
            return need_retrieve, query, reason
        except Exception as exc:
            logger.debug("RetrievalGate failed open: %s", exc)
            return True, clean, f"gate failed open ({type(exc).__name__})"


class SlotGate:
    """Filters retrieved memory hits to keep only high-value items."""

    def __init__(self, *, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def filter_hits(
        self,
        hits: list[MemoryHit],
        *,
        message: str = "",
    ) -> list[MemoryHit]:
        """Keep hits above threshold, rejecting obsolete or low-confidence records."""
        if not hits:
            return []

        selected: list[MemoryHit] = []
        for hit in hits:
            # Drop superseded or expired
            if hit.record.superseded_by:
                continue

            # Base score threshold
            if hit.score < self.threshold and hit.record.confidence < 0.6:
                continue

            selected.append(hit)

        return selected


class AntiPatternFilter:
    """Prevents transient operational status and noise from entering durable memory."""

    @staticmethod
    def is_ops_state(content: str) -> bool:
        """Returns True if content describes transient infrastructure or ops state."""
        return any(pattern.search(content) for pattern in _OPS_STATE_PATTERNS)

    @staticmethod
    def is_meta_state(content: str) -> bool:
        """Returns True if content describes memory store omissions."""
        return any(pattern.search(content) for pattern in _META_MEMORY_PATTERNS)

    @staticmethod
    def is_restatement(content: str, recalled_contents: list[str], threshold: float = 0.65) -> bool:
        """Returns True if content essentially restates an already recalled memory."""
        tokens_a = set(re.findall(r"\w+", content.lower()))
        if not tokens_a:
            return False
        for recalled in recalled_contents:
            tokens_b = set(re.findall(r"\w+", recalled.lower()))
            if not tokens_b:
                continue
            overlap = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
            if overlap >= threshold:
                return True
        return False

    @classmethod
    def is_acceptable(cls, content: str, recalled_contents: list[str] | None = None) -> bool:
        """Check if memory content is acceptable for long-term storage."""
        if cls.is_ops_state(content):
            return False
        if cls.is_meta_state(content):
            return False
        return not (recalled_contents and cls.is_restatement(content, recalled_contents))


def export_memory_markdown(store: MemoryStore, target_path: Path | None = None) -> Path:
    """Export the store's current durable memories into a clean markdown document."""
    records = store.list(limit=1000)
    out_path = target_path or (store.path.parent / "MEMORY.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    by_kind: dict[str, list[MemoryRecord]] = {k: [] for k in MEMORY_KINDS}
    for rec in records:
        if rec.kind in by_kind and not rec.superseded_by:
            by_kind[rec.kind].append(rec)

    lines: list[str] = [
        "# Workspace Long-Term Memory",
        "",
        f"> Exported from `{store.path.name}` for workspace `{store.workspace}`.",
        "",
    ]

    kind_titles = {
        "convention": "Conventions & Architectural Guidelines",
        "decision": "Technical Decisions",
        "preference": "User & Tool Preferences",
        "error": "Resolved Bugs & Known Gotchas",
        "fact": "Project Facts & Reference Data",
        "episode": "Historical Episodes",
    }

    total_exported = 0
    for kind in MEMORY_KINDS:
        recs = by_kind.get(kind, [])
        if not recs:
            continue
        lines.append(f"## {kind_titles.get(kind, kind.title())}")
        lines.append("")
        for r in sorted(recs, key=lambda x: x.created_at, reverse=True):
            tags_str = f" `[{', '.join(r.tags)}]`" if r.tags else ""
            lines.append(f"- **{r.content}**{tags_str} *(id: {r.id[:8]}, conf: {r.confidence:.2f})*")
            total_exported += 1
        lines.append("")

    if total_exported == 0:
        lines.append("*No durable memories recorded yet.*")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
