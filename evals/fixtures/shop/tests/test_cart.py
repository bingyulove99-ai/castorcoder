from shop.cart import Cart, cart_subtotal


def test_subtotal_sums_item_prices():
    cart = Cart()
    cart.add("pen", 250)
    cart.add("pad", 750)

    assert cart_subtotal(cart) == 1000
