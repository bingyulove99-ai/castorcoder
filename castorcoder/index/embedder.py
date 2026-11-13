"""Embedding backends.

The default ``HashingEmbedder`` is deterministic, dependency-free and fully
offline.  It is a lexical hashing model rather than a semantic one, so it is
always combined with BM25 in the retriever.  An OpenAI embedding backend is
available when a caller wants real semantic vectors.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

_TOKEN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_CJK = re.compile(r"[一-鿿]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class Embedder(Protocol):
    name: str
    dim: int
    semantic_weight: float

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class HashingEmbedder:
    """A signed feature-hashing bag-of-tokens embedder."""

    # Measured at v1.3.0 (300 files / 2,061 chunks): vector-only Recall@5 is
    # 40.0%, BM25 is 80.0%, and hybrid is 74.3% at weight 0.1.  The lexical
    # baseline adds little complementary recall, so fusion down-weights it;
    # a real embedding backend uses 1.0.
    semantic_weight = 0.1

    def __init__(self, dim: int = 384) -> None:
        if dim < 16:
            raise ValueError("dim must be at least 16")
        self.dim = dim
        self.name = f"hash-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        counts = Counter(_tokenize(text))
        vector = [0.0] * self.dim
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class OpenAIEmbedder:
    """OpenAI-compatible embedding backend (network required)."""

    semantic_weight = 1.0

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.name = f"openai:{model}"
        self.dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = self.client.embeddings.create(model=self.model, input=batch)
            vectors.extend([list(item.embedding) for item in response.data])
        if vectors and not self.dim:
            self.dim = len(vectors[0])
        return vectors


def tokenize(text: str) -> list[str]:
    """Tokenize code identifiers, prose and CJK characters deterministically."""

    return _tokenize(text)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        for part in raw.split("_"):
            for piece in _CAMEL_BOUNDARY.split(part):
                lowered = piece.lower()
                if len(lowered) >= 2 or lowered.isdigit():
                    tokens.append(lowered)
    tokens.extend(_CJK.findall(text))
    return tokens


def make_embedder(spec: str | None = None, *, api_key: str | None = None,
                  base_url: str | None = None) -> Embedder:
    """Create an embedder from ``hash``, ``hash:<dim>`` or ``openai:<model>``."""

    value = (spec or "hash").strip()
    if value in {"", "hash", "default"}:
        return HashingEmbedder()
    if value.startswith("hash:"):
        return HashingEmbedder(int(value.split(":", 1)[1]))
    if value == "openai" or value.startswith("openai:"):
        model = "text-embedding-3-small"
        if ":" in value:
            model = value.split(":", 1)[1]
        return OpenAIEmbedder(model, api_key=api_key, base_url=base_url)
    raise ValueError(f"Unknown embedding backend: {value}")
