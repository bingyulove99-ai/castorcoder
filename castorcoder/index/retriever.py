"""Hybrid lexical/vector retrieval with reciprocal rank fusion."""

from __future__ import annotations

from dataclasses import dataclass

from ..ranking import rrf_fuse
from .chunker import Chunk
from .store import CodeStore


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float
    sources: tuple[str, ...]

    @property
    def location(self) -> str:
        return f"{self.chunk.path}:{self.chunk.start_line}-{self.chunk.end_line}"


def hybrid_search(
    store: CodeStore,
    query: str,
    *,
    k: int = 8,
    candidates: int = 50,
    path_prefix: str | None = None,
    mode: str = "hybrid",
    exclude_prefixes: tuple[str, ...] | list[str] = (),
) -> list[SearchHit]:
    """Fuse BM25 and vector rankings, then return the best ``k`` chunks.

    ``mode`` also accepts ``bm25`` or ``vector`` so retrieval ablations can
    measure each channel against the fused baseline.
    """

    query = query.strip()
    if not query:
        return []

    lexical = store.search_lexical(query, limit=candidates) if mode != "vector" else {}
    vector = (
        store.search_vectors(store.embedder.embed([query])[0], limit=candidates)
        if mode != "bm25"
        else {}
    )
    if mode == "bm25":
        scores = {identifier: float(len(lexical) - rank) for rank, identifier in enumerate(lexical)}
    elif mode == "vector":
        scores = {identifier: float(len(vector) - rank) for rank, identifier in enumerate(vector)}
    else:
        scores = rrf_fuse(
            [lexical, vector],
            weights=[1.0, getattr(store.embedder, "semantic_weight", 1.0)],
        )
    if not scores:
        return []

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ordered = ordered[: max(k, candidates)]
    ids = [identifier for identifier, _ in ordered]
    by_id = store.get_chunk_map(ids)

    hits: list[SearchHit] = []
    for identifier, score in ordered:
        chunk = by_id.get(identifier)
        if chunk is None:
            continue
        if path_prefix and not chunk.path.startswith(path_prefix):
            continue
        if exclude_prefixes and any(
            chunk.path.startswith(prefix) for prefix in exclude_prefixes
        ):
            continue
        sources = []
        if identifier in lexical:
            sources.append("bm25")
        if identifier in vector:
            sources.append("vector")
        hits.append(SearchHit(chunk=chunk, score=score, sources=tuple(sources)))
        if len(hits) >= k:
            break
    return hits


def render_hits(hits: list[SearchHit], *, max_chars: int = 6_000) -> str:
    if not hits:
        return "No matching code found. Try a broader query or build the index with /index."

    parts: list[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        symbol = f" ({hit.chunk.symbol})" if hit.chunk.symbol else ""
        header = (
            f"[{index}] {hit.location}{symbol} "
            f"score={hit.score:.4f} sources={','.join(hit.sources)}"
        )
        budget = max_chars - used - len(header) - 8
        if budget <= 0:
            parts.append("... (result budget exhausted)")
            break
        body = hit.chunk.content
        if len(body) > budget:
            body = body[:budget] + "\n... (chunk truncated)"
        block = f"{header}\n{body}"
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
