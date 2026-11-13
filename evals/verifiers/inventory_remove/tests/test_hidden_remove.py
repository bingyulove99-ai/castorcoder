import pytest

from inventory.core import Inventory
from inventory.service import restock_plan


def test_remove_reduces_stock():
    inventory = Inventory()
    inventory.add("bolt", 10, 25)

    inventory.remove("bolt", 3)

    assert inventory.get("bolt").quantity == 7


def test_remove_exact_stock_deletes_item():
    inventory = Inventory()
    inventory.add("bolt", 2, 25)

    inventory.remove("bolt", 2)

    assert inventory.get("bolt") is None


def test_remove_unknown_item_raises_keyerror():
    with pytest.raises(KeyError):
        Inventory().remove("ghost", 1)


def test_remove_more_than_stock_raises_valueerror_and_preserves_state():
    inventory = Inventory()
    inventory.add("bolt", 2, 25)

    with pytest.raises(ValueError):
        inventory.remove("bolt", 5)

    assert inventory.get("bolt").quantity == 2


def test_remove_non_positive_quantity_raises_valueerror():
    inventory = Inventory()
    inventory.add("bolt", 2, 25)

    with pytest.raises(ValueError):
        inventory.remove("bolt", 0)


def test_restock_plan_reports_unknown_items_as_full_requests():
    inventory = Inventory()
    inventory.add("bolt", 1, 25)

    assert restock_plan(inventory, {"bolt": 5, "nut": 4}) == [("bolt", 4), ("nut", 4)]
