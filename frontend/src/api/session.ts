// The logged-in session, kept in memory only. The access token is never put in localStorage,
// where any script on the page could read it. The refresh token is an httpOnly cookie that only
// the browser holds, so after a reload POST /auth/refresh brings the session back.
import type { Schemas } from './types'

export type User = Schemas['UserOut']
export type Session = { accessToken: string; user: User }

let current: Session | null = null
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((listener) => listener())
}

export function getSession(): Session | null {
  return current
}

/** Start or renew the session from a login, register or refresh response. */
export function setSession(tokens: Schemas['TokenResponse']): void {
  current = { accessToken: tokens.access_token, user: tokens.user }
  notify()
}

export function clearSession(): void {
  if (current === null) return
  current = null
  notify()
}

/** For React: `useSyncExternalStore(subscribeSession, getSession)`. */
export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
