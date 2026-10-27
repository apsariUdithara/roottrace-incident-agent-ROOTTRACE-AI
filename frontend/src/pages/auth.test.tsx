// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearSession, getSession, setSession } from '@/api/session'
import { safeNext } from '@/lib/auth'
import { queryClient } from '@/lib/query-client'
import { routes } from '@/routes'

const ALICE = { email: 'alice@example.com', password: 'correct horse battery' }

function tokens(email: string, accessToken: string) {
  const user = { id: 'u1', email, created_at: '2026-10-27T10:00:00Z' }
  return { access_token: accessToken, token_type: 'bearer' as const, expires_in: 900, user }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A stand-in for the backend's auth routes: Alice has an account, and the refresh cookie is
 * valid once someone logs in or registers (or from the start, with `cookie: true`). */
function backend({ cookie = false } = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const route = `${request.method} ${new URL(request.url).pathname.replace(/^\/api/, '')}`
      calls.push(route)
      const body = request.method === 'POST' ? await request.text() : ''
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
        case 'GET /health':
          return json(200, { status: 'ok' })
        default:
          return json(404, { detail: 'Not Found', code: 'not_found' })
      }
    }),
  )
  return calls
}

function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const here = (router: ReturnType<typeof renderApp>) =>
  router.state.location.pathname + router.state.location.search

async function logIn(email: string, password: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  vi.unstubAllGlobals()
})

describe('logging in', () => {
  it('sends a visitor without a session to the login page, and back after logging in', async () => {
    backend()
    const router = renderApp('/?tab=tests')

    expect(await screen.findByText('Log in to RootTrace')).toBeTruthy()
    expect(here(router)).toBe('/login?next=%2F%3Ftab%3Dtests')

    await logIn(ALICE.email, ALICE.password)

    expect(await screen.findByText(ALICE.email)).toBeTruthy() // the header
    expect(here(router)).toBe('/?tab=tests')
    expect(await screen.findByText('ok')).toBeTruthy() // the page itself loaded its data
  })

  it('keeps a reloaded page logged in, using the refresh cookie', async () => {
    const calls = backend({ cookie: true })
    const router = renderApp('/')

    expect(await screen.findByText(ALICE.email)).toBeTruthy()
    expect(here(router)).toBe('/')
    expect(calls[0]).toBe('POST /auth/refresh')
    expect(screen.queryByText('Log in to RootTrace')).toBeNull()
  })

  it("shows the backend's message for a wrong password, and stays on the page", async () => {
    const calls = backend()
    const router = renderApp('/login')

    await logIn(ALICE.email, 'wrong password')

    expect((await screen.findByRole('alert')).textContent).toBe('Incorrect email or password')
    expect(here(router)).toBe('/login')
    expect(getSession()).toBeNull()
    expect(calls).not.toContain('POST /auth/refresh') // a failed login is not an expired token
  })

  it('sends someone already logged in straight on', async () => {
    backend()
    setSession(tokens(ALICE.email, 'token'))
    const router = renderApp('/login?next=%2F%3Ftab%3Dreport')

    expect(await screen.findByText(ALICE.email)).toBeTruthy()
    expect(here(router)).toBe('/?tab=report')
  })
})

describe('registering', () => {
  it('explains a taken email, then creates the account and logs in', async () => {
    backend()
    const router = renderApp('/register')
    const user = userEvent.setup()
    const email = await screen.findByLabelText('Email')
    const password = screen.getByLabelText('Password')
    expect(password.getAttribute('minlength')).toBe('8')
    expect(password.getAttribute('autocomplete')).toBe('new-password')

    await user.type(email, ALICE.email)
    await user.type(password, 'a long new password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'An account with this email already exists',
    )

    await user.clear(email)
    await user.type(email, 'bob@example.com')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('bob@example.com')).toBeTruthy()
    expect(here(router)).toBe('/')
  })
})

describe('leaving', () => {
  it('logs out: ends the session, drops cached data and shows the login page', async () => {
    const calls = backend({ cookie: true })
    const router = renderApp('/')
    expect(await screen.findByText('ok')).toBeTruthy()
    expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Log out' }))

    expect(await screen.findByText('Log in to RootTrace')).toBeTruthy()
    expect(here(router)).toBe('/login')
    expect(calls).toContain('POST /auth/logout')
    expect(getSession()).toBeNull()
    expect(queryClient.getQueryCache().getAll()).toEqual([])
  })

  it('shows the login page as soon as a session ends while in use, with the way back', async () => {
    backend({ cookie: true })
    const router = renderApp('/?tab=tests')
    expect(await screen.findByText(ALICE.email)).toBeTruthy()

    act(() => clearSession()) // what the API client does when a refresh fails

    expect(await screen.findByText('Log in to RootTrace')).toBeTruthy()
    expect(here(router)).toBe('/login?next=%2F%3Ftab%3Dtests')
  })
})

describe('safeNext', () => {
  it('keeps pages of this app', () => {
    expect(safeNext('/investigations/i1?tab=tests#log')).toBe('/investigations/i1?tab=tests#log')
    expect(safeNext(null)).toBe('/')
  })

  it.each(['https://evil.example', '//evil.example', '/\\evil.example', 'javascript:alert(1)'])(
    'never leaves the app for %s',
    (next) => {
      expect(safeNext(next)).toBe('/')
    },
  )
})
