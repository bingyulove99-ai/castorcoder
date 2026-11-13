"""Aggregation helpers for expense records."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .models import Expense


def in_range(expense: Expense, start: date, end: date) -> bool:
    """Return True when the expense was recorded inside the period."""
    return start < expense.spent_on < end


def monthly_total(expenses: Iterable[Expense], start: date, end: date) -> int:
    """Total amount in cents for expenses between ``start`` and ``end``."""
    return sum(expense.amount_cents for expense in expenses if in_range(expense, start, end))


def top_categories(expenses: Iterable[Expense], limit: int = 3) -> list[tuple[str, int]]:
    """Return ``(category, total_cents)`` pairs, largest first."""
    totals: dict[str, int] = {}
    for expense in expenses:
        totals[expense.category] = totals.get(expense.category, 0) + expense.amount_cents
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def category_breakdown(expenses: Iterable[Expense]) -> dict[str, dict[str, float]]:
    """Return per-category totals and their share of the grand total."""
    raise NotImplementedError("category_breakdown is not implemented yet")
