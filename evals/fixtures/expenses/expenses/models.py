"""Expense records used by the reporting module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Expense:
    id: str
    category: str
    amount_cents: int
    spent_on: date
    note: str = ""
