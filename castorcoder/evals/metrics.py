"""Ranking metrics used by retrieval and memory evaluations."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def evaluate_ranking(
    gold: Sequence[str],
    hits: Sequence[T],
    *,
    k: int,
    matcher: Callable[[str, T], bool],
) -> dict[str, float]:
    """Compute Recall@k, MRR and nDCG@k for one query."""

    if not gold:
        return {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    top = list(hits[: max(1, k)])
    matched: set[str] = set()
    first_rank = 0
    dcg = 0.0
    for rank, hit in enumerate(top, start=1):
        relevance = 0
        for expected in gold:
            if expected in matched:
                continue
            if matcher(expected, hit):
                relevance = 1
                matched.add(expected)
                break
        if relevance:
            if not first_rank:
                first_rank = rank
            dcg += 1.0 / math.log2(rank + 1)

    ideal = min(len(gold), max(1, k))
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal + 1))
    return {
        "recall": len(matched) / len(gold),
        "mrr": (1.0 / first_rank) if first_rank else 0.0,
        "ndcg": (dcg / idcg) if idcg else 0.0,
    }


def mean_metrics(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    return {
        key: sum(row.get(key, 0.0) for row in rows) / len(rows)
        for key in ("recall", "mrr", "ndcg")
    }


def gold_matches_code(gold: str, hit) -> bool:
    """Match ``path``, ``path:line`` or ``path#symbol`` against a SearchHit."""

    chunk = hit.chunk
    if "#" in gold:
        path, _, symbol = gold.partition("#")
        return chunk.path == path and chunk.symbol == symbol
    if ":" in gold:
        path, _, line = gold.rpartition(":")
        if line.isdigit():
            number = int(line)
            return chunk.path == path and chunk.start_line <= number <= chunk.end_line
    return chunk.path == gold


def gold_matches_memory(gold: str, hit) -> bool:
    return gold.lower() in hit.record.content.lower()


def wilson_interval(successes: int, attempts: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial pass@1 estimate."""

    if attempts <= 0:
        return (0.0, 0.0)
    successes = max(0, min(successes, attempts))
    p = successes / attempts
    denominator = 1.0 + z * z / attempts
    centre = (p + z * z / (2 * attempts)) / denominator
    half = (
        z
        * math.sqrt(p * (1.0 - p) / attempts + z * z / (4.0 * attempts * attempts))
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolation percentile (0 <= fraction <= 1)."""

    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)
