"""Shopping cart."""

from __future__ import annotations

from dataclasses import dataclass, field

from shop.pricing import discounted_total


@dataclass
class Cart:
    items: list[tuple[str, int]] = field(default_factory=list)

    def add(self, name: str, price_cents: int) -> None:
        self.items.append((name, price_cents))


def cart_subtotal(cart: Cart) -> int:
    return sum(price for _, price in cart.items)


def checkout(cart: Cart, percent_off: int = 0) -> int:
    return discounted_total(cart, percent_off)
