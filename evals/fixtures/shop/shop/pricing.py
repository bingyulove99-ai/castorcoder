"""Price calculations."""

from __future__ import annotations

from shop.cart import Cart, cart_subtotal


def discounted_total(cart: Cart, percent_off: int) -> int:
    if not 0 <= percent_off <= 100:
        raise ValueError("percent_off must be between 0 and 100")
    subtotal = cart_subtotal(cart)
    return subtotal - (subtotal * percent_off) // 100
