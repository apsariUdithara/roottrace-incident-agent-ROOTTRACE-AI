# Database

PayStream uses a single Postgres primary. Tests use SQLite in memory with the same migrations.

## Connection pooling

Each API instance keeps its own connection pool, so the database sees:

```
total connections = DB_POOL_SIZE × INSTANCES
```

This total **must stay at or below Postgres `max_connections`** (currently 100). With 5 instances, the **recommended pool size is 20 per instance or less**.

If the total exceeds `max_connections`, Postgres refuses new connections with `FATAL: sorry, too many clients already` (logged by our driver as `OperationalError: too many connections`). Requests then wait for a pool slot until `DB_POOL_TIMEOUT_SECONDS` (5 s) and fail with HTTP 500.

Raising the pool does **not** add capacity. To handle more traffic, add read replicas or raise `max_connections` on the server first (a change owned by the platform team), then resize pools to match.

| Setting | Where | Default |
|---|---|---|
| `PAYSTREAM_DB_POOL_SIZE` | `database/config.py` | 20 |
| `PAYSTREAM_INSTANCES` | deploy config | 5 |
| `PAYSTREAM_DB_MAX_CONNECTIONS` | deploy config, mirrors the server setting | 100 |

## Schema and migrations

Migrations live in `database/migrations.py` and run in order.

| Migration | Purpose |
|---|---|
| 0001 | `users` table |
| 0002 | `payments` table |
| 0003 | `ix_users_name` on `users(name)`: required by user search in the support console |
| 0004 | `ix_payments_user_id` on `payments(user_id)` |

`ix_users_name` looks unused in application code but is what keeps `users.search_by_name` fast. Without it every search scans the whole `users` table (about 2 million rows in production), which takes several seconds and times out the support console.

## Query guidelines

- Every query used on a request path must be backed by an index. Check with `EXPLAIN` (Postgres) or `EXPLAIN QUERY PLAN` (SQLite).
- Do not drop an index without checking `pg_stat_user_indexes` in production first.
