from decimal import Decimal

from paystream.payments.service import calculate_total


def test_total_is_exact_for_cents():
    items = [{"price": "0.10", "quantity": 1}, {"price": "0.20", "quantity": 1}]
    assert calculate_total(items) == Decimal("0.30")
