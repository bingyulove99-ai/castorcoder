"""Report rendering."""

from __future__ import annotations

from collections.abc import Iterable

from .aggregate import mean


def summarize(values: Iterable[float]) -> dict[str, float | int]:
    items = list(values)
    return {"avg": mean(items), "count": len(items)}
