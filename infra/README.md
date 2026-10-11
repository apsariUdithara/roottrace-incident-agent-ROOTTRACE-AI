# infra

Everything needed to run the stack, locally and on the Nebius AI Cloud VM. See [IMPLEMENTATION_PLAN.md §14](../docs/IMPLEMENTATION_PLAN.md#14-observability-security-deployment).

- `docker-compose.yml`: local development (PostgreSQL 16 + pgvector).
- `docker-compose.prod.yml`: production on the Nebius VM (Caddy, backend, PostgreSQL).
- `Caddyfile`: TLS, the static frontend, and the `/api` reverse proxy.
- `sandbox.Dockerfile`: optional custom base image for Nebius Sandboxes.

Owner: Person B.

## Local database

From the repo root:

```bash
docker compose -f infra/docker-compose.yml up -d --wait   # start and wait until healthy
docker compose -f infra/docker-compose.yml down           # stop (data is kept)
docker compose -f infra/docker-compose.yml down -v        # stop and wipe the data volume
```

The defaults match `DATABASE_URL` in `backend/.env.example`. If port 5432 is taken, run it with `POSTGRES_PORT=5433` and change `DATABASE_URL` to match.
