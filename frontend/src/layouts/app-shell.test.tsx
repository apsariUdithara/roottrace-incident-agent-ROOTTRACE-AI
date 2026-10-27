// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import { queryClient } from '@/lib/query-client'
import { backend, here, project, renderApp, stubMatchMedia } from '@/test/helpers'

const PAYSTREAM = project('p1', 'PayStream')
const CHECKOUT = project('p2', 'Checkout')

beforeEach(() => stubMatchMedia())

afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const nav = () => within(screen.getByRole('list'))

/** Whether a section is marked current, both for screen readers and on screen. */
function isCurrent(name: string): boolean {
  const link = nav().getByRole('link', { name })
  const announced = link.getAttribute('aria-current') === 'page'
  const highlighted = link.hasAttribute('data-active')
  expect(highlighted).toBe(announced) // the two must never disagree
  return announced
}

describe('sidebar', () => {
  it('has its sections, marks the current one, and navigates', async () => {
    backend({ cookie: true })
    const router = renderApp('/')
    await screen.findByRole('heading', { name: 'Dashboard' })

    const sections = nav().getAllByRole('link').map((link) => link.textContent)
    expect(sections).toEqual(['Dashboard', 'Projects', 'Knowledge', 'Evaluation', 'Chat'])
    expect(isCurrent('Dashboard')).toBe(true)
    expect(isCurrent('Knowledge')).toBe(false)

    await userEvent.setup().click(nav().getByRole('link', { name: 'Knowledge' }))

    expect(await screen.findByRole('heading', { name: 'Knowledge' })).toBeTruthy()
    expect(here(router)).toBe('/knowledge')
    expect(isCurrent('Knowledge')).toBe(true)
    expect(isCurrent('Dashboard')).toBe(false) // "/" matches exactly, not every page
  })

  it('collapses to icons with its button', async () => {
    backend({ cookie: true })
    renderApp('/')
    await screen.findByRole('heading', { name: 'Dashboard' })
    const sidebar = () => document.querySelector('[data-slot="sidebar"]')
    expect(sidebar()?.getAttribute('data-state')).toBe('expanded')

    // Two controls toggle it: the top bar's button, and the rail along the sidebar's edge.
    const toggles = screen.getAllByRole('button', { name: 'Toggle Sidebar' })
    const topBarButton = toggles.find((button) => button.dataset.sidebar === 'trigger')!
    await userEvent.setup().click(topBarButton)

    expect(sidebar()?.getAttribute('data-state')).toBe('collapsed')
  })

  it("says which task builds a page that isn't built yet", async () => {
    backend({ cookie: true })
    renderApp('/evaluation')

    expect(await screen.findByRole('heading', { name: 'Evaluation' })).toBeTruthy()
    expect(screen.getByText(/Coming with B-902/)).toBeTruthy()
  })
})

describe('project switcher', () => {
  it("lists the user's projects, starts on the first, and remembers a choice", async () => {
    backend({ cookie: true, projects: [CHECKOUT, PAYSTREAM] })
    renderApp('/')
    const picker = (await screen.findByRole('combobox', { name: 'Project' })) as HTMLSelectElement

    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Checkout',
      'PayStream',
    ])
    expect(picker.value).toBe('p2')

    await userEvent.setup().selectOptions(picker, 'p1')
    expect(picker.value).toBe('p1')

    cleanup() // a reload: the choice is still there
    renderApp('/')
    const reloaded = (await screen.findByRole('combobox', { name: 'Project' })) as HTMLSelectElement
    expect(reloaded.value).toBe('p1')
  })

  it("falls back to the first project when the remembered one isn't this user's", async () => {
    localStorage.setItem('roottrace.project', 'someone-elses-project')
    backend({ cookie: true, projects: [CHECKOUT, PAYSTREAM] })
    renderApp('/')

    const picker = (await screen.findByRole('combobox', { name: 'Project' })) as HTMLSelectElement
    expect(picker.value).toBe('p2')
  })

  it('offers to create a project when there are none', async () => {
    backend({ cookie: true, projects: [] })
    renderApp('/')

    const create = await screen.findByRole('link', { name: 'Create a project' })
    expect(create.getAttribute('href')).toBe('/projects')
  })
})
