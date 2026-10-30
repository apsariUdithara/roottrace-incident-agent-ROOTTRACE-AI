// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import { queryClient } from '@/lib/query-client'
import { backend, here, json, project, renderApp, stubMatchMedia, tokens } from '@/test/helpers'

const INV = {
  id: 'i1', project_id: 'p1', question: 'Payment API returning 500s', status: 'completed',
  root_cause: 'pool too big', confidence: 0.94, report: null, started_at: '2026-10-25T04:00:00Z',
  finished_at: '2026-10-25T04:03:00Z', total_tokens: 1000, total_cost_usd: 0.05,
}
const STATS = {
  investigations: 12, avg_confidence: 0.88, avg_duration_ms: 198_000, avg_cost_usd: 0.068,
  patch_success_rate: 0.81, recent_investigations: [INV],
  recent_incidents: [{ id: 'n1', key: 'INC-017', title: 'Connection pool exhausted', occurred_at: null }],
}

beforeEach(() => stubMatchMedia())
afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  vi.unstubAllGlobals()
})

it('"Try the demo" logs in without a password and opens Investigations', async () => {
  const calls = backend({
    projects: [project('p1', 'PayStream')],
    handle: (route) => {
      if (route === 'POST /auth/demo') return json(200, tokens('demo@roottrace.dev', 'token-demo'))
      if (route === 'GET /projects/p1/investigations') return json(200, [])
    },
  })
  const router = renderApp('/login')

  await userEvent.setup().click(await screen.findByRole('button', { name: /Try the demo/ }))

  expect(await screen.findByRole('heading', { name: 'Investigations' })).toBeTruthy()
  expect(here(router)).toBe('/investigations')
  expect(calls).toContain('POST /auth/demo')
})

it('says why when the demo is not set up', async () => {
  backend({
    handle: (route) =>
      route === 'POST /auth/demo'
        ? json(503, { detail: "The demo isn't set up on this server", code: 'service_unavailable' })
        : undefined,
  })
  renderApp('/login')
  await userEvent.setup().click(await screen.findByRole('button', { name: /Try the demo/ }))
  expect((await screen.findByRole('alert')).textContent).toBe("The demo isn't set up on this server")
})

it('the dashboard shows the averages and links to recent investigations', async () => {
  backend({
    cookie: true,
    projects: [project('p1', 'PayStream')],
    handle: (route, _body, query) =>
      route === 'GET /stats/dashboard' && query.get('project_id') === 'p1' ? json(200, STATS) : undefined,
  })
  renderApp('/')

  expect(await screen.findByText('88%')).toBeTruthy() // avg confidence
  expect(screen.getByText('198 s')).toBeTruthy()
  expect(screen.getByText('81%')).toBeTruthy() // fixes verified
  expect(screen.getByRole('link', { name: /Payment API returning 500s/ }).getAttribute('href')).toBe('/investigations/i1')
  expect(screen.getByText('INC-017')).toBeTruthy()
})

it('the trace tab lists each call with its model tier, time and cost', async () => {
  const span = (name: string, kind: string, extra: object) => ({
    id: name, parent_id: null, kind, name, model: null, tier: null, prompt_version: null,
    started_at: '2026-10-25T04:00:00Z', latency_ms: 1000, prompt_tokens: 0, completion_tokens: 0,
    cost_usd: 0, cached: false, downgraded: false, status: 'ok', error: null, input: null, output: null,
    ...extra,
  })
  backend({
    cookie: true,
    projects: [project('p1', 'PayStream')],
    handle: (route) => {
      if (route === 'GET /investigations/i1') return json(200, INV)
      if (route === 'GET /investigations/i1/events') return json(200, [])
      if (route === 'GET /investigations/i1/trace') {
        return json(200, {
          spans: [
            span('hypothesize', 'llm', { tier: 'ultra', model: 'nvidia/Nemotron-3-Ultra-550b-a55b', cost_usd: 0.012 }),
            span('run_tests', 'sandbox', { latency_ms: 9000, started_at: '2026-10-25T04:00:01Z' }),
          ],
          totals: { spans: 2, llm_calls: 1, prompt_tokens: 900, completion_tokens: 100, cost_usd: 0.012, duration_ms: 10_000 },
        })
      }
    },
  })
  renderApp('/investigations/i1')

  await userEvent.setup().click(await screen.findByRole('tab', { name: 'Trace' }))

  const trace = within(await screen.findByRole('table', { name: 'Trace' }))
  expect(trace.getByText('hypothesize')).toBeTruthy()
  expect(trace.getByText(/ultra/)).toBeTruthy()
  expect(trace.getByText(/\$0\.0120/)).toBeTruthy()
  expect(screen.getByText(/2 calls · 1 to Nemotron · 1,000 tokens/)).toBeTruthy()
})
