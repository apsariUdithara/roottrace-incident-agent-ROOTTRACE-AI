# infra

Everything needed to run the stack, locally and on the Nebius AI Cloud VM. See [IMPLEMENTATION_PLAN.md §14](../docs/IMPLEMENTATION_PLAN.md#14-observability-security-deployment).

- `docker-compose.yml`: local development (PostgreSQL 16 + pgvector).
- `docker-compose.prod.yml`: production on the Nebius VM (Caddy, backend, PostgreSQL).
- `Caddyfile`: TLS, the static frontend, and the `/api` reverse proxy.
- `sandbox.Dockerfile`: optional custom base image for Nebius Sandboxes.

Owner: Person B.
