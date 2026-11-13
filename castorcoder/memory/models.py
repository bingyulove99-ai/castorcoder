"""Long-term memory data model."""

from __future__ import annotations

from dataclasses import asdict, dataclass

MEMORY_KINDS = (
    "episode",
    "decision",
    "convention",
    "preference",
    "error",
    "fact",
)


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: str
    content: str
    source: str = ""
    tags: tuple[str, ...] = ()
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None
    superseded_by: str | None = None

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    sources: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "record": self.record.as_dict(),
            "score": self.score,
            "sources": list(self.sources),
        }
