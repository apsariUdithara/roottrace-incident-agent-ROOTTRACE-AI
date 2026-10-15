"""Order totals and charges.

Money is always handled as Decimal, never float (see docs/payments.md).
"""
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def calculate_total(items: list[dict]) -> Decimal:
    """Sum price * quantity for each item, rounded to cents."""
    total = sum((Decimal(str(i["price"])) * i["quantity"] for i in items), Decimal("0"))
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Charge:
    id: str
    amount: Decimal
    currency: str
    idempotency_key: str


@dataclass
class ChargeStore:
    """In-memory store; production uses the payments table."""
    charges: dict[str, Charge] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)


def create_charge(store: ChargeStore, amount: Decimal, currency: str, idempotency_key: str) -> Charge:
    """Create a charge. Retrying with the same idempotency key returns the original charge."""
    if idempotency_key in store.by_key:
        return store.charges[store.by_key[idempotency_key]]
    charge = Charge(f"ch_{uuid.uuid4().hex[:12]}", amount, currency, idempotency_key)
    store.charges[charge.id] = charge
    store.by_key[idempotency_key] = charge.id
    return charge
