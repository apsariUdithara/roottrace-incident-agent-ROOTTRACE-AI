# API reference

All amounts are decimal strings. Errors use standard HTTP status codes.

## `GET /health`

Returns `{"status": "ok"}`.

## `POST /orders/total`

Body: a list of items.

```json
[{"price": "4.50", "quantity": 2}, {"price": "0.99", "quantity": 1}]
```

Response: `{"total": "9.99"}`.

## `POST /payments`

Headers: `Idempotency-Key` (required).

Body: `{"amount": "25.00", "currency": "USD"}`.

Response: `{"id": "ch_...", "amount": "25.00", "currency": "USD"}`. Repeating the request with the same `Idempotency-Key` returns the same charge.

## `POST /webhooks/gateway`

Called by the card gateway.

```json
{"type": "charge.succeeded", "data": {"id": "ch_123", "metadata": {"order_id": "ord_9"}}}
```

`data.metadata` may be missing or `null`. Response: `{"type": ..., "payment_id": ..., "order_id": ...}`; `order_id` is `null` when there is no metadata. Missing `type` or `data.id` returns 400.

## Status codes

| Code | Meaning |
|---|---|
| 400 | Malformed request |
| 401 | Missing, expired or invalid session token |
| 500 | Unexpected server error, including database connection timeouts |
