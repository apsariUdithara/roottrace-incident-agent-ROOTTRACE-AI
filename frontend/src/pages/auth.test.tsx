// @vitest-environment jsdom
import { act, cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession, getSession, setSession } from '@/api/session'
import { safeNext } from '@/lib/auth'
import { queryClient } from '@/lib/query-client'
import { ALICE, backend, here, project, renderApp, stubMatchMedia, tokens } from '@/test/helpers'

async function logIn(email: string, password: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

beforeEach(() => stubMatchMedia())

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

    expect(await screen.findByText(ALICE.email)).toBeTruthy() // the top bar
    expect(here(router)).toBe('/?tab=tests')
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeTruthy()
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
    const calls = backend({ cookie: true, projects: [project('p1', 'PayStream')] })
    const router = renderApp('/')
    expect(await screen.findByRole('combobox', { name: 'Project' })).toBeTruthy()
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
