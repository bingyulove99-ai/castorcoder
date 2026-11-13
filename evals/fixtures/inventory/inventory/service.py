"""Warehouse restock planning."""

from __future__ import annotations

from .core import Inventory


def restock_plan(inventory: Inventory, requests: dict[str, int]) -> list[tuple[str, int]]:
    """Return ``(name, quantity_needed)`` pairs for requested items."""
    plan: list[tuple[str, int]] = []
    for name, wanted in requests.items():
        item = inventory.get(name)
        if item is None:
            continue
        if item.quantity < wanted:
            plan.append((name, wanted - item.quantity))
    return plan
