import pytest

from shop.cart import Cart, checkout
from shop.pricing import discounted_total


def test_checkout_applies_percentage_discount():
    cart = Cart()
    cart.add("a", 1000)
    cart.add("b", 500)

    assert checkout(cart, 10) == 1350


def test_zero_discount_returns_subtotal():
    cart = Cart()
    cart.add("a", 999)

    assert checkout(cart) == 999


def test_invalid_percent_is_rejected():
    with pytest.raises(ValueError):
        discounted_total(Cart(), 101)
