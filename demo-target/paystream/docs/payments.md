# Payments

## Money handling

All money is represented as `decimal.Decimal`, never `float`.

Floats are binary and cannot store most decimal amounts exactly: `0.1 + 0.2` is `0.30000000000000004` as a float. Summed over an order, these errors produce totals that differ from the item prices customers see, and they break reconciliation with the gateway, which works in exact cents.

Rules:

1. Parse prices with `Decimal(str(value))`, never `Decimal(float_value)`.
2. Round once, at the end, to cents with `ROUND_HALF_UP` (`CENT = Decimal("0.01")`).
3. Serialize amounts as strings in JSON (`"9.00"`), never as numbers.

`calculate_total` in `payments/service.py` implements these rules.

## Idempotency

Every `POST /payments` must carry an `Idempotency-Key` header, usually the order ID. Checkout retries on network errors, and the mobile app retries when the user taps "Pay" twice.

`create_charge` looks up the key first: if a charge already exists for it, it returns that charge instead of creating another. **This lookup is what prevents duplicate charges.** Removing it means every retry charges the customer again.

## Charge lifecycle

`pending` → `succeeded` or `failed`, driven by gateway webhooks. See [architecture.md](architecture.md#webhooks).
