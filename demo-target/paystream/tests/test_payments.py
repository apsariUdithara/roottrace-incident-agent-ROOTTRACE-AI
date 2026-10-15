from decimal import Decimal

from paystream.payments.service import ChargeStore, calculate_total, create_charge


def test_total_single_item():
    assert calculate_total([{"price": "10.00", "quantity": 2}]) == Decimal("20.00")


def test_total_empty_order():
    assert calculate_total([]) == Decimal("0.00")


def test_create_charge_returns_id():
    charge = create_charge(ChargeStore(), Decimal("12.50"), "USD", "key-1")
    assert charge.id.startswith("ch_")
    assert charge.amount == Decimal("12.50")
