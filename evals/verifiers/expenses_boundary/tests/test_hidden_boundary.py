from datetime import date

from expenses.models import Expense
from expenses.report import monthly_total


def expense(day: int, cents: int = 100) -> Expense:
    return Expense(id=f"e{day}", category="food", amount_cents=cents, spent_on=date(2026, 1, day))


def test_period_includes_both_boundaries():
    expenses = [expense(1), expense(15), expense(31)]

    assert monthly_total(expenses, date(2026, 1, 1), date(2026, 1, 31)) == 300


def test_expenses_outside_the_period_are_excluded():
    expenses = [expense(1), expense(2), expense(3)]

    assert monthly_total(expenses, date(2026, 1, 2), date(2026, 1, 3)) == 200


def test_empty_input_is_zero():
    assert monthly_total([], date(2026, 1, 1), date(2026, 1, 31)) == 0
