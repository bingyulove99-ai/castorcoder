"""Semantic/lexical code search over the local workspace index."""

from __future__ import annotations

from ..index.indexer import CodeIndex
from ..index.retriever import render_hits
from .base import Tool


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Search the workspace code and documentation index using hybrid BM25 + "
        "vector retrieval. Returns file:line ranges and matched excerpts."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 300.0
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or identifier query",
            },
            "k": {
                "type": "integer",
                "description": "Maximum number of chunks to return (default 8)",
            },
            "path": {
                "type": "string",
                "description": "Optional path prefix filter, e.g. 'src/'",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, index: CodeIndex) -> None:
        self.index = index

    def execute(self, query: str, k: int = 8, path: str | None = None) -> str:
        try:
            hits = self.index.search(query, k=max(1, min(int(k), 20)), path_prefix=path)
        except Exception as exc:  # indexing must never crash the agent loop
            return f"Error: code search failed: {type(exc).__name__}: {exc}"
        return render_hits(hits)
