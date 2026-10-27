// The API client: typed requests (openapi-fetch over the generated schema) sent through a fetch
// that adds the access token and, when it has expired, refreshes the session once and retries.
//
//   const projects = await ok(api.GET('/projects'))
//   useQuery({ queryKey: ['projects'], queryFn: () => ok(api.GET('/projects')) })
import createClient from 'openapi-fetch'

import type { paths } from './schema'
import { clearSession, getSession, setSession } from './session'
import type { Schemas } from './types'

/** Vite (in development) and Caddy (in production) proxy this prefix to the backend. */
export const API_BASE = '/api'
// Absolute where there's a page, so Request() also accepts it in tests (jsdom on Node).
const BASE_URL = typeof location === 'undefined' ? API_BASE : `${location.origin}${API_BASE}`

// A 401 from these means a wrong password or no session at all, not an expired access token.
const NO_REFRESH = new Set(['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'])

let refreshing: Promise<boolean> | null = null

/** Get a new access token with the refresh cookie. Requests that hit a 401 at the same time
 * share one refresh. False, and the session is cleared, if the cookie is missing or expired. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= requestNewToken().finally(() => {
    refreshing = null
  })
  return refreshing
}

async function requestNewToken(): Promise<boolean> {
  const response = await fetch(`${BASE_URL}/auth/refresh`, { method: 'POST' })
  if (!response.ok) {
    clearSession()
    return false
  }
  setSession((await response.json()) as Schemas['TokenResponse'])
  return true
}

/** fetch for API requests: sends `Authorization: Bearer <token>`. On a 401 it refreshes the
 * session once and retries; if that fails, the caller gets the 401. */
export async function authFetch(request: Request): Promise<Response> {
  const retry = request.clone() // a request body can be read only once: keep one for the retry
  const response = await fetch(withToken(request))
  if (response.status !== 401 || NO_REFRESH.has(apiPath(request))) return response
  if (!(await refreshSession())) return response
  return fetch(withToken(retry))
}

function withToken(request: Request): Request {
  const token = getSession()?.accessToken
  if (token) request.headers.set('Authorization', `Bearer ${token}`)
  return request
}

function apiPath(request: Request): string {
  const path = new URL(request.url).pathname // "/api/auth/login" -> "/auth/login"
  return path.startsWith(API_BASE) ? path.slice(API_BASE.length) : path
}

export const api = createClient<paths>({ baseUrl: BASE_URL, fetch: authFetch })

type FieldError = { field: string; message: string }

/** A non-2xx response. `message` is the backend's `detail`, readable as it is; `code` is
 * machine-readable (backend/app/core/errors.py); `fieldErrors` explain a 422. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: FieldError[]

  constructor(status: number, detail: string, code: string, fieldErrors: FieldError[] = []) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
  }
}

type ErrorBody = { detail: string; code: string; errors?: FieldError[] }

function isErrorBody(body: unknown): body is ErrorBody {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as ErrorBody).detail === 'string' &&
    typeof (body as ErrorBody).code === 'string'
  )
}

/** The response's data, or a thrown ApiError: what query and mutation functions need. */
export async function ok<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request
  if (response.ok) return data as T
  if (isErrorBody(error)) {
    throw new ApiError(response.status, error.detail, error.code, error.errors)
  }
  // Not the backend's JSON (e.g. a proxy's 502 page): the status is all we know.
  throw new ApiError(response.status, response.statusText || `HTTP ${response.status}`, 'error')
}
