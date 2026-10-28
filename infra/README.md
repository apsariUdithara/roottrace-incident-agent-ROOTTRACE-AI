# infra

Everything needed to run the stack, locally and on the Nebius AI Cloud VM. See [IMPLEMENTATION_PLAN.md §14](../docs/IMPLEMENTATION_PLAN.md#14-observability-security-deployment).

- `docker-compose.yml`: local development (PostgreSQL 16 + pgvector).
- `Caddyfile`: the public entry point: the built frontend, the `/api` reverse proxy, and TLS. It's baked into the frontend image.
- `docker-compose.prod.yml` (B-512): production on the Nebius VM (frontend/Caddy, backend, PostgreSQL).

No custom sandbox image is needed: the sandbox runs tests on the public `python:3.12` image.

Owner: Person B.

## Local database

From the repo root:

```bash
docker compose -f infra/docker-compose.yml up -d --wait   # start and wait until healthy
docker compose -f infra/docker-compose.yml down           # stop (data is kept)
docker compose -f infra/docker-compose.yml down -v        # stop and wipe the data volume
```

The defaults match `DATABASE_URL` in `backend/.env.example`. If port 5432 is taken, run it with `POSTGRES_PORT=5433` and change `DATABASE_URL` to match.

## Docker images

Both build from the repo root. CI builds them on every PR.

```bash
docker build -f backend/Dockerfile -t roottrace-backend .
docker build -f frontend/Dockerfile -t roottrace-frontend .
```

**`roottrace-backend`** (about 670 MB) runs the API and the agent on port 8000.
- **Contents:** Python 3.12 with git and the locked dependencies (no dev tools).
- **User:** runs as the non-root user `roottrace`.
- **Health check:** on `/health`.
- **PayStream repo:** built during the image build and checked release by release, at `/app/demo-target/build/paystream`. The build fails if any release lost its bug. Fixed dates and authors keep its commit SHAs the same on every rebuild, so an indexed project stays valid.
- **Migrations:** `docker run --env-file backend/.env --entrypoint alembic roottrace-backend upgrade head`.
- **Clones** of connected repositories go in `/app/backend/data` (`DATA_DIR`). Mount a volume there, or they're cloned again after each deploy.
- **Read-only role:** set `READONLY_DATABASE_URL` (see `backend/.env.example`) before running `seed_demo.py`. The seed creates the role and its grants, and the agent's SQL tool then runs as it.
- **Secrets:** settings come from the environment only. `.env` files never go into an image (`.dockerignore`). Pass the `.env` as the container's environment (`--env-file`, or Compose `env_file:`), so the sandbox CLI also sees `NEBIUS_API_KEY` and `NEBIUS_AI_PROJECT`.

**`roottrace-frontend`** (about 95 MB) is Caddy serving the built app on port 80, with `/api/*` proxied to the backend.
- **Settings:** `BACKEND_ADDRESS` (default `backend:8000`, the compose service name) and `SITE_ADDRESS`. Set `SITE_ADDRESS` to the public hostname and Caddy gets a TLS certificate by itself; unset, it serves plain HTTP.
- **Routing:** the `/api` prefix is stripped, as in the Vite dev server. Server-Sent Events are passed on unbuffered (`flush_interval -1`).
- **Pages:** any other path serves `index.html`, so deep links work. Hashed assets are cached for a year.

Try the pair locally against the dev database (from the repo root):

```bash
docker run -d --name rt-backend --network roottrace_default --network-alias backend \
  --env-file backend/.env -e DATABASE_URL=postgresql+asyncpg://roottrace:roottrace@postgres:5432/roottrace \
  roottrace-backend
docker run -d --name rt-frontend --network roottrace_default -p 8080:80 roottrace-frontend
# http://localhost:8080 ; stop with: docker rm -f rt-backend rt-frontend
```
