"""Read and write the agent's long-term memory."""

from __future__ import annotations

from ..memory.models import MEMORY_KINDS
from ..memory.store import MemoryStore
from .base import Tool


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = (
        "Search long-term memory for prior decisions, conventions, errors and "
        "user preferences. Returns memory ids, kinds and provenance."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to recall"},
            "k": {"type": "integer", "description": "Maximum memories (default 6)"},
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Optional memory kind filter",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def execute(self, query: str, k: int = 6, kind: str | None = None) -> str:
        hits = self.store.search(
            query,
            k=max(1, min(int(k), 20)),
            kinds=(kind,) if kind else None,
        )
        if not hits:
            return "No relevant memories found."
        lines = []
        for hit in hits:
            record = hit.record
            source = f" source={record.source}" if record.source else ""
            lines.append(
                f"- [{record.kind}] id={record.id} confidence={record.confidence:.2f}"
                f"{source}: {record.content}"
            )
        return "\n".join(lines)


class UpdateMemoryTool(Tool):
    name = "update_memory"
    description = (
        "Add, list, forget or garbage-collect long-term memories. Use add for "
        "durable decisions, conventions and user preferences."
    )
    risk = "write"
    parallel_safe = False
    timeout_s = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "forget", "gc", "stats"],
            },
            "content": {"type": "string", "description": "Memory text for action=add"},
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Memory kind for action=add/list",
            },
            "source": {"type": "string", "description": "Provenance for action=add"},
            "confidence": {
                "type": "number",
                "description": "Confidence between 0 and 1 (default 0.5)",
            },
            "ttl_days": {
                "type": "number",
                "description": "Optional expiry in days",
            },
            "memory_id": {"type": "string", "description": "Id for action=forget"},
            "limit": {"type": "integer", "description": "Limit for action=list"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def execute(
        self,
        action: str,
        content: str | None = None,
        kind: str | None = None,
        source: str = "",
        confidence: float | None = None,
        ttl_days: float | None = None,
        memory_id: str | None = None,
        limit: int = 20,
    ) -> str:
        if action == "add":
            if not content:
                return "Error: action=add requires content"
            record, created = self.store.add(
                content,
                kind=kind or "fact",
                source=source,
                confidence=0.5 if confidence is None else max(0.0, min(1.0, confidence)),
                ttl_days=ttl_days,
            )
            verb = "Added" if created else "Updated"
            return f"{verb} memory {record.id} [{record.kind}]."
        if action == "list":
            records = self.store.list(kind=kind, limit=max(1, min(int(limit), 100)))
            if not records:
                return "Memory is empty."
            return "\n".join(
                f"- id={record.id} [{record.kind}] confidence={record.confidence:.2f}: "
                f"{record.content}"
                for record in records
            )
        if action == "forget":
            if not memory_id:
                return "Error: action=forget requires memory_id"
            return "Forgotten." if self.store.forget(memory_id) else "Memory not found."
        if action == "gc":
            return f"Garbage-collected {self.store.gc()} memories."
        if action == "stats":
            stats = self.store.stats()
            return (
                f"Memories: {stats['active']} active / {stats['total']} total "
                f"({stats['embedder']}, {stats['path']})"
            )
        return f"Error: unknown action '{action}'"
