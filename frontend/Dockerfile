# syntax=docker/dockerfile:1
# The web app, served by Caddy, which also proxies /api to the backend (infra/Caddyfile).
# Build from the repo root:
#   docker build -f frontend/Dockerfile -t roottrace-frontend .

FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend ./
RUN npm run build

FROM caddy:2-alpine
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
