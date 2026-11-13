"""Local code index: AST-aware chunking, SQLite FTS5 and hybrid retrieval."""

from .chunker import Chunk, chunk_file
from .embedder import Embedder, HashingEmbedder, OpenAIEmbedder, make_embedder
from .indexer import CodeIndex, IndexStats
from .retriever import SearchHit, hybrid_search, render_hits
from .store import CodeStore

__all__ = [
    "Chunk",
    "CodeIndex",
    "CodeStore",
    "Embedder",
    "HashingEmbedder",
    "IndexStats",
    "OpenAIEmbedder",
    "SearchHit",
    "chunk_file",
    "hybrid_search",
    "make_embedder",
    "render_hits",
]
