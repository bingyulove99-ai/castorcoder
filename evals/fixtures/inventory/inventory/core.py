"""Core inventory data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    name: str
    quantity: int
    price_cents: int


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add(self, name: str, quantity: int, price_cents: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        existing = self._items.get(name)
        if existing is None:
            self._items[name] = Item(name=name, quantity=quantity, price_cents=price_cents)
        else:
            existing.quantity += quantity
            existing.price_cents = price_cents

    def get(self, name: str) -> Item | None:
        return self._items.get(name)

    def total_value_cents(self) -> int:
        return sum(item.quantity * item.price_cents for item in self._items.values())
