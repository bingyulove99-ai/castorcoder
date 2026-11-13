"""Incremental workspace indexing and retrieval facade."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..workspace import Workspace
from .chunker import chunk_file
from .embedder import Embedder, HashingEmbedder
from .retriever import SearchHit, hybrid_search
from .store import CodeStore

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tmp",
    ".castorcoder",
}
SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".7z",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".mp3",
}
# Files that commonly hold credentials.  The code index runs automatically in
# the background, so it must never copy `.env` contents into a searchable DB.
SKIP_NAMES = {
    ".env",
    ".netrc",
    ".pgpass",
    "credentials",
    "secrets",
    ".castorcoder.toml",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
MAX_FILE_BYTES = 1_000_000


@dataclass
class IndexStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    chunks: int = 0
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CodeIndex:
    """A local, incremental code index scoped to one workspace."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        *,
        db_path: str | Path | None = None,
        embedder: Embedder | None = None,
        max_file_bytes: int = MAX_FILE_BYTES,
        auto_build: bool = True,
    ) -> None:
        self.workspace = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        self.embedder = embedder or HashingEmbedder()
        self.max_file_bytes = max(1_000, max_file_bytes)
        self.auto_build = auto_build
        path = Path(db_path) if db_path else self.workspace.root / ".castorcoder" / "index.db"
        self.db_path = path.expanduser().resolve()
        self.store = CodeStore(
            self.db_path,
            workspace=str(self.workspace.root),
            embedder=self.embedder,
        )
        self._index_checked = False

    def update(self, *, force: bool = False) -> IndexStats:
        started = time.perf_counter()
        stats = IndexStats()
        known = self.store.known_file_hashes()
        seen: set[str] = set()

        for path in self._iter_files():
            stats.files_seen += 1
            try:
                relative = path.relative_to(self.workspace.root).as_posix()
            except ValueError:
                continue
            seen.add(relative)
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if not force and known.get(relative) == digest:
                stats.files_unchanged += 1
                continue
            if (
                len(raw) > self.max_file_bytes
                or path.suffix.lower() in SKIP_SUFFIXES
                or b"\x00" in raw[:8_192]
            ):
                self.store.delete_file(relative)
                continue

            chunks = chunk_file(path, rel_path=relative)
            if not chunks:
                self.store.delete_file(relative)
                continue
            self.store.replace_file(relative, digest, chunks)
            stats.files_indexed += 1

        for relative in sorted(set(known) - seen):
            self.store.delete_file(relative)
            stats.files_removed += 1

        stats.chunks = int(self.store.stats()["chunks"])
        stats.duration_ms = (time.perf_counter() - started) * 1000
        return stats

    def ensure_indexed(self) -> IndexStats | None:
        if self._index_checked:
            return None
        self._index_checked = True
        if int(self.store.stats()["chunks"]) > 0 or not self.auto_build:
            return None
        return self.update()

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        path_prefix: str | None = None,
        mode: str = "hybrid",
        exclude_prefixes: tuple[str, ...] | list[str] = (),
    ) -> list[SearchHit]:
        self.ensure_indexed()
        return hybrid_search(
            self.store,
            query,
            k=k,
            path_prefix=path_prefix,
            mode=mode,
            exclude_prefixes=exclude_prefixes,
        )

    def stats(self) -> dict[str, object]:
        result = self.store.stats()
        result["workspace"] = str(self.workspace.root)
        return result

    def clear(self) -> None:
        self.store.clear()

    def _iter_files(self):
        root = self.workspace.root
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in relative_parts):
                continue
            lower_name = path.name.lower()
            if (
                lower_name in SKIP_NAMES
                or lower_name.startswith(".env.")
                or path.suffix.lower() in SECRET_SUFFIXES
            ):
                continue
            if path.name.endswith(("~", ".tmp", ".swp")):
                continue
            if not self.workspace.contains(path):
                continue
            yield path
