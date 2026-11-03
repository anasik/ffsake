#!/usr/bin/env python3
"""Small shipping example used for a second debugger fixture."""

from dataclasses import dataclass


FREE_SHIPPING_MINIMUM = 50.0
SHIPPING_FEE = 5.99


@dataclass
class Cart:
    name: str
    items: list[float]


def subtotal(cart):
    return round(sum(cart.items), 2)


def shipping_fee(amount):
    if amount > FREE_SHIPPING_MINIMUM:
        return 0.0
    return SHIPPING_FEE


def total(cart):
    amount = subtotal(cart)
    return round(amount + shipping_fee(amount), 2)


def main():
    carts = [
        Cart("ana", [25.0, 25.0]),
        Cart("ben", [10.0, 15.0]),
    ]
    expected = {"ana": 50.0, "ben": 30.99}
    actual = {cart.name: total(cart) for cart in carts}
    print(f"actual={actual}")
    print(f"expected={expected}")
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
