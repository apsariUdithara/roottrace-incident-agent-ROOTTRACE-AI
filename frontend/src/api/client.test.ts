import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { shouldRetry } from '@/lib/query-client'

import { ApiError, authFetch, ok } from './client'
import { clearSession, getSession, setSession, subscribeSession } from './session'

const USER = { id: 'u1', email: 'alice@example.com', created_at: '2026-10-27T10:00:00Z' }

function tokens(accessToken: string) {
  return { access_token: accessToken, token_type: 'bearer' as const, expires_in: 900, user: USER }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const UNAUTHORIZED = () => json(401, { detail: 'Invalid or expired token', code: 'unauthorized' })

/** A fake backend: /auth/refresh answers `refresh()`; other routes answer `route(request)`. */
function backend(
  route: (request: Request) => Response | Promise<Response>,
  refresh: () => Response = () => json(200, tokens('fresh')),
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input)
    if (url.endsWith('/auth/refresh')) return refresh()
    return route(input as Request)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const refreshCalls = (fetchMock: ReturnType<typeof backend>) =>
  fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/auth/refresh')).length

const bearer = (request: Request) => request.headers.get('Authorization')

const get = (path: string) => new Request(`http://localhost/api${path}`)

beforeEach(() => clearSession())
afterEach(() => vi.unstubAllGlobals())

describe('authFetch', () => {
  it('sends the access token when logged in, and nothing when not', async () => {
    const seen: (string | null)[] = []
    backend((request) => {
      seen.push(bearer(request))
      return json(200, [])
    })

    await authFetch(get('/projects'))
    setSession(tokens('abc'))
    await authFetch(get('/projects'))

    expect(seen).toEqual([null, 'Bearer abc'])
  })

  it('refreshes once on a 401 and retries with the new token, body included', async () => {
    setSession(tokens('expired'))
    const seen: { auth: string | null; body: string }[] = []
    const fetchMock = backend(async (request) => {
      seen.push({ auth: bearer(request), body: await request.text() })
      return bearer(request) === 'Bearer fresh' ? json(202, { id: 'i1' }) : UNAUTHORIZED()
    })

    const response = await authFetch(
      new Request('http://localhost/api/projects/p1/investigations', {
        method: 'POST',
        body: JSON.stringify({ question: 'Why 500s?' }),
      }),
    )

    expect(response.status).toBe(202)
    expect(seen).toEqual([
      { auth: 'Bearer expired', body: '{"question":"Why 500s?"}' },
      { auth: 'Bearer fresh', body: '{"question":"Why 500s?"}' },
    ])
    expect(refreshCalls(fetchMock)).toBe(1)
    expect(getSession()?.accessToken).toBe('fresh')
  })

  it('shares one refresh between requests that fail at the same time', async () => {
    setSession(tokens('expired'))
    const fetchMock = backend((request) =>
      bearer(request) === 'Bearer fresh' ? json(200, []) : UNAUTHORIZED(),
    )

    const responses = await Promise.all([
      authFetch(get('/projects')),
      authFetch(get('/auth/me')),
      authFetch(get('/projects/p1/investigations')),
    ])

    expect(responses.map((r) => r.status)).toEqual([200, 200, 200])
    expect(refreshCalls(fetchMock)).toBe(1)
  })

  it('ends the session when the refresh cookie is gone, and returns the 401', async () => {
    setSession(tokens('expired'))
    const fetchMock = backend(UNAUTHORIZED, UNAUTHORIZED)
    const changes = vi.fn()
    const unsubscribe = subscribeSession(changes)

    const response = await authFetch(get('/projects'))

    expect(response.status).toBe(401)
    expect(getSession()).toBeNull()
    expect(changes).toHaveBeenCalledOnce() // the app can send the user to the login page
    expect(fetchMock).toHaveBeenCalledTimes(2) // the request and one refresh, no retry
    unsubscribe()
  })

  it('never refreshes for a failed login', async () => {
    const fetchMock = backend(() =>
      json(401, { detail: 'Incorrect email or password', code: 'unauthorized' }),
    )

    const response = await authFetch(new Request('http://localhost/api/auth/login', { method: 'POST' }))

    expect(response.status).toBe(401)
    expect(refreshCalls(fetchMock)).toBe(0)
  })
})

describe('ok', () => {
  const result = (status: number, data?: unknown, error?: unknown) =>
    Promise.resolve({ data, error, response: new Response(null, { status }) })

  it('returns the data of a successful response', async () => {
    expect(await ok(result(200, { status: 'ok' }))).toEqual({ status: 'ok' })
  })

  it("throws the backend's error as an ApiError", async () => {
    const thrown = await ok(
      result(422, undefined, {
        detail: 'Request validation failed',
        code: 'validation_error',
        errors: [{ field: 'body.question', message: 'String should have at least 5 characters' }],
      }),
    ).catch((error: unknown) => error)

    expect(thrown).toBeInstanceOf(ApiError)
    expect(thrown).toMatchObject({
      status: 422,
      message: 'Request validation failed',
      code: 'validation_error',
      fieldErrors: [{ field: 'body.question', message: 'String should have at least 5 characters' }],
    })
  })

  it('still throws an ApiError for a body that is not the backend JSON', async () => {
    const thrown = await ok(result(502, undefined, '<html>Bad Gateway</html>')).catch(
      (error: unknown) => error,
    )

    expect(thrown).toMatchObject({ status: 502, code: 'error', message: 'HTTP 502' })
  })
})

describe('shouldRetry', () => {
  it('retries a server or network error once, and never a 4xx', () => {
    expect(shouldRetry(0, new ApiError(503, 'Service Unavailable', 'service_unavailable'))).toBe(true)
    expect(shouldRetry(1, new ApiError(503, 'Service Unavailable', 'service_unavailable'))).toBe(false)
    expect(shouldRetry(0, new TypeError('Failed to fetch'))).toBe(true)
    expect(shouldRetry(0, new ApiError(404, 'Project not found', 'not_found'))).toBe(false)
    expect(shouldRetry(0, new ApiError(429, 'Limit reached', 'too_many_requests'))).toBe(false)
  })
})
