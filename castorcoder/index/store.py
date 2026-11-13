"""SQLite-backed chunk and embedding store for code retrieval."""

from __future__ import annotations

import array
import contextlib
import hashlib
import heapq
import re
import sqlite3
import time
from pathlib import Path

from .chunker import Chunk
from .embedder import Embedder

_QUERY_TOKEN = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")
_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
    "id UNINDEXED, workspace UNINDEXED, content, symbol, path)"
)


class CodeStore:
    """Persist chunks, lexical index and vectors in one portable database."""

    def __init__(
        self,
        path: str | Path,
        workspace: str,
        embedder: Embedder,
    ) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            ws_hash = hashlib.md5(workspace.encode("utf-8")).hexdigest()[:10]
            self.path = Path.home() / ".castorcoder" / "workspaces" / ws_hash / "index.db"
            with contextlib.suppress(OSError):
                self.path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.embedder = embedder
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
                CREATE TABLE IF NOT EXISTS files (
                    workspace TEXT NOT NULL,
                    path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (workspace, path)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    path TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'text',
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_workspace_path
                    ON chunks(workspace, path);
                CREATE TABLE IF NOT EXISTS embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_embeddings_workspace_model
                    ON embeddings(workspace, model);
                """
            )
            try:
                connection.execute(_FTS_SCHEMA)
            except sqlite3.OperationalError:
                self.fts_enabled = False

    def known_file_hashes(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, file_hash FROM files WHERE workspace = ?",
                (self.workspace,),
            ).fetchall()
        return {row["path"]: row["file_hash"] for row in rows}

    def replace_file(self, rel_path: str, file_hash: str, chunks: list[Chunk]) -> None:
        now = time.time()
        with self._connect() as connection:
            old_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM chunks WHERE workspace = ? AND path = ?",
                    (self.workspace, rel_path),
                )
            ]
            self._delete_ids(connection, old_ids)
            connection.execute(
                "DELETE FROM chunks WHERE workspace = ? AND path = ?",
                (self.workspace, rel_path),
            )
            for chunk in chunks:
                chunk_id = self._chunk_id(chunk)
                content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT OR REPLACE INTO chunks "
                    "(id, workspace, path, symbol, kind, start_line, end_line, "
                    "content, content_hash, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk_id,
                        self.workspace,
                        chunk.path,
                        chunk.symbol,
                        chunk.kind,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content,
                        content_hash,
                        now,
                    ),
                )
                if self.fts_enabled:
                    connection.execute(
                        "INSERT INTO chunks_fts (id, workspace, content, symbol, path) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            chunk_id,
                            self.workspace,
                            chunk.content,
                            chunk.symbol,
                            chunk.path,
                        ),
                    )
            if chunks:
                vectors = self.embedder.embed([chunk.content for chunk in chunks])
                for chunk, vector in zip(chunks, vectors, strict=False):
                    chunk_id = self._chunk_id(chunk)
                    connection.execute(
                        "INSERT OR REPLACE INTO embeddings "
                        "(chunk_id, workspace, model, dim, vector) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            chunk_id,
                            self.workspace,
                            self.embedder.name,
                            len(vector),
                            array.array("f", vector).tobytes(),
                        ),
                    )
            connection.execute(
                "INSERT OR REPLACE INTO files (workspace, path, file_hash, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (self.workspace, rel_path, file_hash, now),
            )

    def delete_file(self, rel_path: str) -> None:
        with self._connect() as connection:
            old_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM chunks WHERE workspace = ? AND path = ?",
                    (self.workspace, rel_path),
                )
            ]
            self._delete_ids(connection, old_ids)
            connection.execute(
                "DELETE FROM chunks WHERE workspace = ? AND path = ?",
                (self.workspace, rel_path),
            )
            connection.execute(
                "DELETE FROM files WHERE workspace = ? AND path = ?",
                (self.workspace, rel_path),
            )

    def _delete_ids(self, connection: sqlite3.Connection, ids: list[str]) -> None:
        if not ids:
            return
        connection.executemany(
            "DELETE FROM embeddings WHERE chunk_id = ?", [(item,) for item in ids]
        )
        if self.fts_enabled:
            connection.executemany(
                "DELETE FROM chunks_fts WHERE id = ?", [(item,) for item in ids]
            )

    def search_lexical(self, query: str, limit: int = 50) -> list[str]:
        if not self.fts_enabled:
            return self._search_like(query, limit)
        match = self._fts_query(query)
        if not match:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id FROM chunks_fts "
                    "WHERE chunks_fts MATCH ? AND workspace = ? "
                    "ORDER BY bm25(chunks_fts) LIMIT ?",
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
                f"SELECT id FROM chunks WHERE workspace = ? AND {clause} LIMIT ?",
                [self.workspace, *params, limit],
            ).fetchall()
        return [row["id"] for row in rows]

    def search_vectors(self, vector: list[float], limit: int = 50) -> list[str]:
        if not vector:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT chunk_id, vector FROM embeddings "
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
            scored.append((score, row["chunk_id"]))
        return [item[1] for item in heapq.nlargest(limit, scored)]

    def get_chunks(self, ids: list[str]) -> list[Chunk]:
        by_id = self.get_chunk_map(ids)
        return [by_id[item] for item in ids if item in by_id]

    def get_chunk_map(self, ids: list[str]) -> dict[str, Chunk]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {row["id"]: _row_to_chunk(row) for row in rows}

    def stats(self) -> dict[str, object]:
        with self._connect() as connection:
            files = connection.execute(
                "SELECT COUNT(*) FROM files WHERE workspace = ?", (self.workspace,)
            ).fetchone()[0]
            chunks = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE workspace = ?", (self.workspace,)
            ).fetchone()[0]
            vectors = connection.execute(
                "SELECT COUNT(*) FROM embeddings WHERE workspace = ?", (self.workspace,)
            ).fetchone()[0]
        return {
            "path": str(self.path),
            "files": files,
            "chunks": chunks,
            "vectors": vectors,
            "fts": self.fts_enabled,
            "embedder": self.embedder.name,
        }

    def clear(self) -> None:
        with self._connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM chunks WHERE workspace = ?", (self.workspace,)
                )
            ]
            self._delete_ids(connection, ids)
            connection.execute("DELETE FROM chunks WHERE workspace = ?", (self.workspace,))
            connection.execute("DELETE FROM files WHERE workspace = ?", (self.workspace,))

    def _chunk_id(self, chunk: Chunk) -> str:
        material = (
            f"{self.workspace}|{chunk.path}|{chunk.start_line}|{chunk.end_line}|"
            f"{chunk.symbol}|{hashlib.sha256(chunk.content.encode('utf-8')).hexdigest()}"
        )
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = []
        for token in _QUERY_TOKEN.findall(query):
            if len(token) < 2 and not token.isdigit():
                continue
            tokens.append(token.replace('"', ""))
        if not tokens:
            return ""
        return " OR ".join(f'"{token}"*' for token in tokens[:32])


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        path=row["path"],
        symbol=row["symbol"],
        kind=row["kind"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        content=row["content"],
    )
