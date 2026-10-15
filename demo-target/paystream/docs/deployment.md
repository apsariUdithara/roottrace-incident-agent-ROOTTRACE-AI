# Deployment

PayStream deploys to 5 instances. Each deploy is a rolling restart; a full rollout takes about 10 minutes.

## Configuration

Configuration comes only from environment variables. The production values live in `deploy/production.env`; secret values are references to the vault and are resolved at deploy time.

Every name in `settings.REQUIRED_ENV` must be present in `deploy/production.env`:

| Variable | Purpose |
|---|---|
| `PAYSTREAM_DATABASE_URL` | Postgres connection string |
| `PAYSTREAM_GATEWAY_API_KEY` | Card gateway API key (vault) |
| `PAYSTREAM_WEBHOOK_SECRET` | Webhook signature secret (vault) |
| `PAYSTREAM_INSTANCES` | Number of API instances |
| `PAYSTREAM_DB_MAX_CONNECTIONS` | Mirrors Postgres `max_connections` |

**Renaming a variable is a two-step change.** Code and deploy config ship separately, so update `deploy/production.env` (and the vault path) in the same release as the code, or support both names for one release. If the code asks for a name production doesn't set, `settings.get` raises `MissingSetting` and every request that needs it fails.

## Checklist

1. All tests pass in CI.
2. `deploy/production.env` defines every name in `REQUIRED_ENV`.
3. Database migrations reviewed: no dropped indexes without production stats.
4. Watch error rate and p95 latency for 15 minutes after the rollout.

## Rollback

Redeploy the previous release tag. Migrations are forward-only, so a dropped index must be re-created by a new migration.
