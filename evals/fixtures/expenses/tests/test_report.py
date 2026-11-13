from datetime import date

from expenses.models import Expense
from expenses.report import monthly_total, top_categories


def make(day: int, cents: int, category: str = "food") -> Expense:
    return Expense(id=f"e{day}", category=category, amount_cents=cents, spent_on=date(2026, 1, day))


def test_total_for_expenses_inside_the_period():
    expenses = [make(10, 500), make(20, 700)]

    assert monthly_total(expenses, date(2026, 1, 1), date(2026, 1, 31)) == 1200


def test_top_categories_orders_by_total():
    expenses = [make(2, 300, "food"), make(3, 900, "travel"), make(4, 100, "food")]

    assert top_categories(expenses) == [("travel", 900), ("food", 400)]
