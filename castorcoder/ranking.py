"""Shared ranking helpers for hybrid lexical/vector retrieval."""

from __future__ import annotations


def rrf_fuse(
    rankings: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """Reciprocal rank fusion over several ordered identifier lists.

    RRF is deliberately simple: it needs no score calibration between BM25 and
    cosine similarity, only the order produced by each retriever.  A larger
    ``k`` flattens the contribution of the top ranks.  ``weights`` lets a caller
    down-weight a channel that is known to be weaker (for example the offline
    hashing embedder).
    """

    if weights is not None and len(weights) != len(rankings):
        raise ValueError("weights must match the number of rankings")
    scores: dict[str, float] = {}
    for position, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[position]
        if weight == 0:
            continue
        for rank, identifier in enumerate(ranking, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + weight / (k + rank)
    return scores
