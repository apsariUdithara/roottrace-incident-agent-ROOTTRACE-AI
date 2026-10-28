// Shared by the component tests (jsdom): a stand-in backend, and the real app routes mounted in
// a memory router.
import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'

import type { Schemas } from '@/api/types'
import { queryClient } from '@/lib/query-client'
import { routes } from '@/routes'

export const ALICE = { email: 'alice@example.com', password: 'correct horse battery' }

export function tokens(email: string, accessToken: string) {
  const user = { id: 'u1', email, created_at: '2026-10-27T10:00:00Z' }
  return { access_token: accessToken, token_type: 'bearer' as const, expires_in: 900, user }
}

export function project(id: string, name: string): Schemas['ProjectOut'] {
  return { id, name, description: '', created_at: '2026-10-27T10:00:00Z' }
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type ChatHandler = (body: Schemas['ChatRequest']) => Response
/** Answers a route the stand-in doesn't know, e.g. "PATCH /projects/p1"; undefined for a 404.
 * `body` is the parsed JSON body, if there is one; `query` the URL's query string. */
export type RouteHandler = (
  route: string,
  body: unknown,
  query: URLSearchParams,
) => Response | undefined

/** A stand-in for the backend: Alice has an account and `projects`. The refresh cookie is valid
 * once someone logs in or registers, or from the start with `cookie: true`. `chat` answers
 * POST /chat, and `handle` any other route. Returns the list of calls made, e.g.
 * "POST /auth/refresh". */
export function backend({
  cookie = false,
  projects = [] as Schemas['ProjectOut'][],
  chat = (() => json(404, { detail: 'Not Found', code: 'not_found' })) as ChatHandler,
  handle = (() => undefined) as RouteHandler,
} = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const route = `${request.method} ${new URL(request.url).pathname.replace(/^\/api/, '')}`
      calls.push(route)
      const body = await request.text()
      switch (route) {
        case 'POST /auth/login': {
          const { email, password } = JSON.parse(body)
          if (email !== ALICE.email || password !== ALICE.password) {
            return json(401, { detail: 'Incorrect email or password', code: 'unauthorized' })
          }
          cookie = true
          return json(200, tokens(email, 'token-login'))
        }
        case 'POST /auth/register': {
          const { email } = JSON.parse(body)
          if (email === ALICE.email) {
            return json(409, { detail: 'An account with this email already exists', code: 'conflict' })
          }
          cookie = true
          return json(201, tokens(email, 'token-register'))
        }
        case 'POST /auth/refresh':
          return cookie
            ? json(200, tokens(ALICE.email, 'token-refreshed'))
            : json(401, { detail: 'Not authenticated', code: 'unauthorized' })
        case 'POST /auth/logout':
          cookie = false
          return new Response(null, { status: 204 })
        case 'GET /projects':
          return json(200, projects)
        case 'POST /chat':
          return chat(JSON.parse(body))
        default:
          return (
            handle(route, body ? JSON.parse(body) : undefined, new URL(request.url).searchParams) ??
            json(404, { detail: 'Not Found', code: 'not_found' })
          )
      }
    }),
  )
  return calls
}

/** jsdom has no matchMedia; the sidebar uses it to tell desktop from mobile. */
export function stubMatchMedia(matches = false) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}

export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

export type AppRouter = ReturnType<typeof renderApp>

export const here = (router: AppRouter) =>
  router.state.location.pathname + router.state.location.search
