from datetime import date

from expenses.models import Expense
from expenses.report import category_breakdown


def test_breakdown_returns_totals_and_shares():
    expenses = [
        Expense("1", "food", 600, date(2026, 1, 2)),
        Expense("2", "travel", 400, date(2026, 1, 3)),
    ]

    result = category_breakdown(expenses)

    assert result["food"]["total_cents"] == 600
    assert result["travel"]["total_cents"] == 400
    assert abs(result["food"]["share"] - 0.6) < 1e-9
    assert abs(result["travel"]["share"] - 0.4) < 1e-9


def test_empty_breakdown_is_empty_mapping():
    assert category_breakdown([]) == {}
