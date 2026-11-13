"""Numeric aggregations."""

from __future__ import annotations

from collections.abc import Iterable


def mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of a non-empty sequence."""
    items = list(values)
    if not items:
        raise ValueError("mean() requires at least one value")
    return sum(items) / len(items)
