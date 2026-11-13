from inventory.core import Inventory
from inventory.service import restock_plan


def test_add_and_total_value():
    inventory = Inventory()
    inventory.add("bolt", 10, 25)
    inventory.add("nut", 4, 10)

    assert inventory.total_value_cents() == 290


def test_restock_plan_covers_shortage():
    inventory = Inventory()
    inventory.add("bolt", 3, 25)

    assert restock_plan(inventory, {"bolt": 8}) == [("bolt", 5)]
