// Logging in and out, and guarding the pages that need a login. The session itself lives in
// src/api/session.ts; this is what pages and routes use.
import { useSyncExternalStore } from 'react'
import { redirect, type LoaderFunctionArgs } from 'react-router'

import { api, ok, refreshSession } from '@/api/client'
import { clearSession, getSession, setSession, subscribeSession } from '@/api/session'

/** The current session, or null; re-renders when it starts or ends. */
export function useSession() {
  return useSyncExternalStore(subscribeSession, getSession)
}

export async function login(email: string, password: string): Promise<void> {
  setSession(await ok(api.POST('/auth/login', { body: { email, password } })))
}

/** "Try demo": logs in as the shared demo user, no password. */
export async function demo(): Promise<void> {
  setSession(await ok(api.POST('/auth/demo')))
}

export async function register(email: string, password: string): Promise<void> {
  setSession(await ok(api.POST('/auth/register', { body: { email, password } })))
}

/** Logs out on the server first, so the refresh cookie is gone before the login page shows.
 * The session here ends even if the server can't be reached. */
export async function logout(): Promise<void> {
  await api.POST('/auth/logout').catch(() => undefined)
  clearSession()
}

/** Loader for the pages that need a login. After a reload the session isn't in memory any
 * more, so it tries the refresh cookie first; otherwise it sends the user to the login page,
 * which brings them back here afterwards. */
export async function requireSession({ request }: LoaderFunctionArgs) {
  if (getSession() || (await restoreSession())) return null
  const { pathname, search } = new URL(request.url)
  throw redirect(loginPath(pathname + search))
}

async function restoreSession(): Promise<boolean> {
  try {
    return await refreshSession()
  } catch {
    return false // the server can't be reached: the login page will say so
  }
}

/** Loader for the login and register pages: someone already logged in goes on to `next`. */
export function guestOnly({ request }: LoaderFunctionArgs) {
  if (!getSession()) return null
  throw redirect(safeNext(new URL(request.url).searchParams.get('next')))
}

export function loginPath(next: string): string {
  return next === '/' ? '/login' : `/login?next=${encodeURIComponent(next)}`
}

const OWN_ORIGIN = 'http://roottrace.invalid'

/** Where to go after logging in: `next` if it's a page of this app, else home. Resolving it as
 * a URL catches every way of pointing elsewhere ("//evil.com", "/\evil.com", "javascript:"). */
export function safeNext(next: string | null): string {
  if (!next) return '/'
  try {
    const url = new URL(next, OWN_ORIGIN)
    return url.origin === OWN_ORIGIN ? url.pathname + url.search + url.hash : '/'
  } catch {
    return '/'
  }
}
