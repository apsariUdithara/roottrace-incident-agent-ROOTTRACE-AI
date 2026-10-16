from decimal import Decimal

from paystream.payments.service import ChargeStore, create_charge


def test_retry_with_same_key_does_not_charge_twice():
    store = ChargeStore()
    first = create_charge(store, Decimal("25.00"), "USD", "order-77")
    second = create_charge(store, Decimal("25.00"), "USD", "order-77")
    assert second.id == first.id
    assert len(store.charges) == 1
