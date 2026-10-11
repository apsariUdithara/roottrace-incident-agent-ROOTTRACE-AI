# frontend

Vite + React + TypeScript app: Tailwind v4 + shadcn/ui (Base UI), TanStack Query, React Router v7 (data mode), Monaco diff, Recharts. See [IMPLEMENTATION_PLAN.md §12](../docs/IMPLEMENTATION_PLAN.md#12-frontend-frontend).

Pages: Login/Register + "Try demo", Dashboard, Investigation, Knowledge, Evaluation.

API and SSE event types are generated from the backend's OpenAPI schema, never written by hand.

Owner: Person B.

## Run locally

Start the backend first (see [backend/README.md](../backend/README.md)). Then, from `frontend/`:

```bash
npm install
npm run dev       # http://localhost:5173 — /api/* is proxied to the backend on :8000
npm run lint
npm run build     # type-check + production build into dist/
```

## Conventions

- Import from `src/` with the `@/` alias, e.g. `import { Button } from '@/components/ui/button'`.
- Add shadcn components with `npx shadcn@latest add <name>`. They land in `src/components/ui/`.
- Routes live in `src/router.tsx`, pages in `src/pages/`, layouts in `src/layouts/`.
- The app always runs in dark mode (`class="dark"` on `<html>`).
