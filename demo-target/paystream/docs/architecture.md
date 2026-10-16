# PayStream architecture

PayStream is the payments API behind checkout. It takes order totals, creates charges against an external card gateway, and receives the gateway's webhook events.

## Components

| Component | Module | Responsibility |
|---|---|---|
| HTTP API | `paystream.app` | FastAPI app exposing `/orders/total`, `/payments`, `/webhooks/gateway`, `/health` |
| Payments | `paystream.payments.service` | Order totals (Decimal) and idempotent charge creation |
| Gateway client | `paystream.payments.gateway` | Calls the card gateway with retries and exponential backoff |
| Webhooks | `paystream.payments.webhooks` | Parses gateway events |
| Auth | `paystream.auth.tokens` | Issues and verifies one-hour session tokens |
| Users | `paystream.users.search` | User lookup by name for the support console |
| Database | `paystream.database` | Connection settings and schema migrations |
| Settings | `paystream.settings` | Environment variables required at runtime |

## Runtime topology

- **5 API instances** behind the load balancer (`PAYSTREAM_INSTANCES`).
- **One Postgres primary** (`max_connections = 100`) shared by all instances. See [database.md](database.md).
- The **card gateway** is a third-party service with its own rate limits. See "Gateway retries" below.

## Request flow: creating a payment

1. Checkout calls `POST /payments` with an `Idempotency-Key` header.
2. `create_charge` returns the existing charge if that key was already used; otherwise it creates one.
3. The gateway client submits the charge, retrying transient failures.
4. The gateway later sends `charge.succeeded` or `charge.failed` to `/webhooks/gateway`.

## Gateway retries

The gateway allows about 50 requests per second per merchant. On `GatewayUnavailable` we retry up to 4 times with exponential backoff (0.5 s, 1 s, 2 s, 4 s, capped at 8 s). Backoff matters: when the gateway is struggling, immediate retries from 5 instances multiply the load and get us rate-limited, which turns a short blip into an outage.

## Webhooks

Gateway events always contain `type` and `data.id`. `data.metadata` is optional: the gateway omits it or sends `null` for charges created outside PayStream, such as refunds issued from the gateway dashboard. Handlers must tolerate a missing or null `metadata`.
