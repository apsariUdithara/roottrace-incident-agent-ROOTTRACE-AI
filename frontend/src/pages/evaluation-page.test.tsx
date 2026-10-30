// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import { queryClient } from '@/lib/query-client'
import { backend, json, renderApp, stubMatchMedia } from '@/test/helpers'

const RUNS = [
  { id: 'new', started_at: '2026-10-25T04:15:25Z', git_sha: 'ad80ac2f', metrics: { cases: 2, root_cause_accuracy: 1, verified_fix_rate: 0.5 } },
  { id: 'old', started_at: '2026-10-24T09:18:53Z', git_sha: '0cdbda2e', metrics: { cases: 2, root_cause_accuracy: 0.5, no_false_fix_rate: null } },
]
const DETAIL = {
  new: {
    ...RUNS[0],
    models: { ultra: 'nvidia/Nemotron-3-Ultra-550b-a55b' },
    cases: [
      { id: 's1-q1', root_cause_correct: true, citations: true, fix: true, fix_status: 'verified', investigation_id: 'i1' },
      { id: 's2-q1', root_cause_correct: true, citations: true, fix: false, fix_status: 'failed' },
    ],
  },
  old: { ...RUNS[1], models: {}, cases: [{ id: 's1-q1', root_cause_correct: false }] },
}

beforeEach(() => stubMatchMedia())
afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  vi.unstubAllGlobals()
})

it('shows the latest run, its failed cases too, and switches to an older run', async () => {
  backend({
    cookie: true,
    handle: (route) => {
      if (route === 'GET /evaluation/runs') return json(200, RUNS)
      const id = route.match(/^GET \/evaluation\/runs\/(\w+)$/)?.[1] as keyof typeof DETAIL | undefined
      return id ? json(200, DETAIL[id]) : undefined
    },
  })
  renderApp('/evaluation')

  expect(await screen.findByText('Root cause correct')).toBeTruthy()
  expect(screen.getByText('50%')).toBeTruthy() // the fix rate: a failure is counted, not hidden
  const table = within(screen.getByRole('table', { name: 'Cases' }))
  expect(table.getByText('failed')).toBeTruthy()
  expect(table.getByRole('link', { name: 's1-q1' }).getAttribute('href')).toBe('/investigations/i1')

  await userEvent.setup().selectOptions(screen.getByRole('combobox', { name: 'Run' }), 'old')
  expect(await screen.findByLabelText('fail')).toBeTruthy()
  expect(screen.getByText('No fix on healthy code').parentElement?.textContent).toContain('—') // null: not measured
})
