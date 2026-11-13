"""SQLite-backed long-term memory with provenance, dedupe and forgetting."""

from __future__ import annotations

import array
import contextlib
import hashlib
import heapq
import re
import sqlite3
import time
from pathlib import Path

from ..index.embedder import Embedder, HashingEmbedder
from ..ranking import rrf_fuse
from .models import MemoryHit, MemoryRecord

_QUERY_TOKEN = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")
_MEMORY_FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
    "id UNINDEXED, workspace UNINDEXED, content, kind)"
)


class MemoryStore:
    """Workspace-scoped memory with lexical + vector retrieval."""

    def __init__(
        self,
        path: str | Path,
        *,
        workspace: str,
        embedder: Embedder | None = None,
    ) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            ws_hash = hashlib.md5(workspace.encode("utf-8")).hexdigest()[:10]
            self.path = Path.home() / ".castorcoder" / "workspaces" / ws_hash / "memory.db"
            with contextlib.suppress(OSError):
                self.path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.embedder = embedder or HashingEmbedder()
        self.fts_enabled = True
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL,
                    superseded_by TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedupe
                    ON memories(workspace, content_hash);
                CREATE INDEX IF NOT EXISTS idx_memories_kind
                    ON memories(workspace, kind);
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                """
            )
            try:
                connection.execute(_MEMORY_FTS)
            except sqlite3.OperationalError:
                self.fts_enabled = False

    def add(
        self,
        content: str,
        *,
        kind: str = "fact",
        source: str = "",
        tags: tuple[str, ...] = (),
        confidence: float = 0.5,
        ttl_days: float | None = None,
    ) -> tuple[MemoryRecord, bool]:
        """Insert a memory, deduplicating by normalized content per workspace."""

        normalized = " ".join(content.split())
        if len(normalized) < 3:
            raise ValueError("memory content is too short")
        content_hash = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
        now = time.time()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM memories WHERE workspace = ? AND content_hash = ?",
                (self.workspace, content_hash),
            ).fetchone()
            if existing:
                confidence_value = max(float(existing["confidence"]), confidence)
                source_value = existing["source"] or source
                connection.execute(
                    "UPDATE memories SET confidence = ?, source = ?, updated_at = ? "
                    "WHERE id = ?",
                    (confidence_value, source_value, now, existing["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM memories WHERE id = ?", (existing["id"],)
                ).fetchone()
                return _row_to_record(row), False

            identifier = hashlib.sha1(
                f"{self.workspace}|{content_hash}".encode()
            ).hexdigest()[:16]
            expires_at = now + ttl_days * 86_400 if ttl_days is not None else None
            connection.execute(
                "INSERT INTO memories "
                "(id, workspace, kind, content, content_hash, source, tags, "
                "confidence, created_at, updated_at, expires_at, superseded_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    identifier,
                    self.workspace,
                    kind,
                    normalized,
                    content_hash,
                    source,
                    ",".join(tags),
                    max(0.0, min(1.0, confidence)),
                    now,
                    now,
                    expires_at,
                ),
            )
            if self.fts_enabled:
                connection.execute(
                    "INSERT INTO memories_fts (id, workspace, content, kind) "
                    "VALUES (?, ?, ?, ?)",
                    (identifier, self.workspace, normalized, kind),
                )
            vector = self.embedder.embed([normalized])[0]
            connection.execute(
                "INSERT INTO memory_embeddings "
                "(memory_id, workspace, model, dim, vector) VALUES (?, ?, ?, ?, ?)",
                (
                    identifier,
                    self.workspace,
                    self.embedder.name,
                    len(vector),
                    array.array("f", vector).tobytes(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (identifier,)
            ).fetchone()
        return _row_to_record(row), True

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND workspace = ?",
                (memory_id, self.workspace),
            ).fetchone()
        return _row_to_record(row) if row else None

    def search(
        self,
        query: str,
        *,
        k: int = 6,
        kinds: tuple[str, ...] | None = None,
    ) -> list[MemoryHit]:
        query = query.strip()
        if not query:
            return []
        lexical = self._search_lexical(query, limit=40)
        vector = self._search_vectors(self.embedder.embed([query])[0], limit=40)
        scores = rrf_fuse([lexical, vector])
        if not scores:
            return []
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)[: max(k * 4, 20)]
        ids = [identifier for identifier, _ in ordered]
        records = self._records_map(ids, kinds=kinds)
        hits: list[MemoryHit] = []
        for identifier, score in ordered:
            record = records.get(identifier)
            if record is None:
                continue
            sources = []
            if identifier in lexical:
                sources.append("bm25")
            if identifier in vector:
                sources.append("vector")
            hits.append(MemoryHit(record=record, score=score, sources=tuple(sources)))
            if len(hits) >= k:
                break
        return hits

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = 50,
        include_expired: bool = False,
    ) -> list[MemoryRecord]:
        clause = "workspace = ? AND superseded_by IS NULL"
        params: list[object] = [self.workspace]
        if kind:
            clause += " AND kind = ?"
            params.append(kind)
        if not include_expired:
            clause += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(time.time())
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories WHERE {clause} "
                "ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def forget(self, memory_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM memories WHERE id = ? AND workspace = ?",
                (memory_id, self.workspace),
            ).fetchone()
            if not row:
                return False
            connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            connection.execute(
                "DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
            )
            if self.fts_enabled:
                connection.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
        return True

    def supersede(self, old_id: str, new_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE memories SET superseded_by = ?, updated_at = ? "
                "WHERE id = ? AND workspace = ?",
                (new_id, time.time(), old_id, self.workspace),
            )
            changed = cursor.rowcount
        return bool(changed)

    def gc(self, *, max_age_days: float = 180.0, now: float | None = None) -> int:
        """Delete expired, superseded and stale low-confidence memories."""

        current = now if now is not None else time.time()
        stale_before = current - max_age_days * 86_400
        low_confidence_before = current - 30 * 86_400
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM memories WHERE workspace = ? AND ("
                "(expires_at IS NOT NULL AND expires_at <= ?) OR "
                "(superseded_by IS NOT NULL AND updated_at <= ?) OR "
                "(confidence < 0.25 AND updated_at <= ?))",
                (self.workspace, current, stale_before, low_confidence_before),
            ).fetchall()
        for row in rows:
            self.forget(row["id"])
        return len(rows)

    def stats(self) -> dict[str, object]:
        with self._connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE workspace = ?", (self.workspace,)
            ).fetchone()[0]
            active = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE workspace = ? AND superseded_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (self.workspace, time.time()),
            ).fetchone()[0]
        return {
            "path": str(self.path),
            "workspace": self.workspace,
            "total": total,
            "active": active,
            "embedder": self.embedder.name,
        }

    def clear(self) -> None:
        with self._connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM memories WHERE workspace = ?", (self.workspace,)
                )
            ]
        for memory_id in ids:
            self.forget(memory_id)

    def _search_lexical(self, query: str, limit: int) -> list[str]:
        if not self.fts_enabled:
            return self._search_like(query, limit)
        match = _fts_query(query)
        if not match:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id FROM memories_fts "
                    "WHERE memories_fts MATCH ? AND workspace = ? "
                    "ORDER BY bm25(memories_fts) LIMIT ?",
                    (match, self.workspace, limit),
                ).fetchall()
            return [row["id"] for row in rows]
        except sqlite3.OperationalError:
            return self._search_like(query, limit)

    def _search_like(self, query: str, limit: int) -> list[str]:
        terms = _QUERY_TOKEN.findall(query.lower())
        if not terms:
            return []
        clause = " AND ".join("lower(content) LIKE ?" for _ in terms)
        params = [f"%{term}%" for term in terms]
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM memories WHERE workspace = ? AND {clause} LIMIT ?",
                [self.workspace, *params, limit],
            ).fetchall()
        return [row["id"] for row in rows]

    def _search_vectors(self, vector: list[float], limit: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT memory_id, vector FROM memory_embeddings "
                "WHERE workspace = ? AND model = ?",
                (self.workspace, self.embedder.name),
            ).fetchall()
        scored: list[tuple[float, str]] = []
        for row in rows:
            stored = array.array("f")
            stored.frombytes(row["vector"])
            if len(stored) != len(vector):
                continue
            score = sum(left * right for left, right in zip(stored, vector, strict=False))
            scored.append((score, row["memory_id"]))
        return [item[1] for item in heapq.nlargest(limit, scored)]

    def _records_map(
        self,
        ids: list[str],
        *,
        kinds: tuple[str, ...] | None,
    ) -> dict[str, MemoryRecord]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        clause = (
            f"workspace = ? AND id IN ({placeholders}) "
            "AND superseded_by IS NULL AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: list[object] = [self.workspace, *ids, time.time()]
        if kinds:
            clause += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories WHERE {clause}", params
            ).fetchall()
        return {row["id"]: _row_to_record(row) for row in rows}


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    tags = tuple(item for item in (row["tags"] or "").split(",") if item)
    return MemoryRecord(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        source=row["source"],
        tags=tags,
        confidence=float(row["confidence"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        expires_at=row["expires_at"],
        superseded_by=row["superseded_by"],
    )


def _fts_query(query: str) -> str:
    tokens: list[str] = []
    for token in _QUERY_TOKEN.findall(query):
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.append(token.replace('"', ""))
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"*' for token in tokens[:32])
