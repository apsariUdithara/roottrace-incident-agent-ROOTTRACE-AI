// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import type { Schemas } from '@/api/types'
import { queryClient } from '@/lib/query-client'
import { SEARCH_DELAY_MS } from '@/lib/search-params'
import { backend, here, json, project, renderApp, stubMatchMedia } from '@/test/helpers'

type LogLine = Schemas['LogEntryOut']

function logLine(id: number, ts: string, fields: Partial<LogLine>): LogLine {
  return {
    id,
    ts,
    level: 'INFO',
    service: 'paystream-api',
    instance: 'api-1',
    route: '/payments',
    status: 200,
    latency_ms: 90,
    message: 'request completed',
    error_type: null,
    trace_id: null,
    attrs: {},
    ...fields,
  }
}

const TOO_MANY =
  'OperationalError: too many connections (FATAL: sorry, too many clients already); pool wait exceeded DB_POOL_TIMEOUT_SECONDS=5'

// From the 6 Oct PayStream logs, oldest first.
const LINES = [
  logLine(1, '2026-10-06T14:19:50Z', { instance: 'api-3', latency_ms: 129, trace_id: '118d101a155b332d' }),
  logLine(2, '2026-10-06T14:20:00Z', {
    instance: 'api-2',
    route: '-',
    status: 0,
    latency_ms: 0,
    message: 'deploy started: release v1.4.8 (rolling, 5 instances)',
    error_type: 'Deploy',
    trace_id: 'd8f16adf91b7584a',
  }),
  logLine(3, '2026-10-06T14:20:16Z', {
    level: 'WARN',
    route: '/webhooks/gateway',
    status: 400,
    latency_ms: 17,
    message: 'request validation failed: missing field',
    error_type: 'RequestValidationError',
    trace_id: '6c0fd4f5f8130c42',
  }),
  logLine(4, '2026-10-06T14:26:27Z', {
    level: 'ERROR',
    instance: 'api-3',
    status: 500,
    latency_ms: 5003,
    message: TOO_MANY,
    error_type: 'OperationalError',
    trace_id: '5a07979ff653e026',
  }),
  logLine(5, '2026-10-06T14:26:28Z', { instance: 'api-3', message: 'payment retried', trace_id: '5a07979ff653e026' }),
]

function facets(lines: LogLine[]): Schemas['LogFacets'] {
  const values = (pick: (line: LogLine) => string | null) => {
    const counts = new Map<string, number>()
    for (const line of lines) {
      const value = pick(line)
      if (value) counts.set(value, (counts.get(value) ?? 0) + 1)
    }
    return [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([value, count]) => ({ value, count }))
  }
  const times = lines.map((line) => line.ts).sort()
  return {
    total: lines.length,
    first_ts: times[0] ?? null,
    last_ts: times.at(-1) ?? null,
    levels: values((line) => line.level.toUpperCase()),
    services: values((line) => line.service),
    routes: values((line) => line.route),
    error_types: values((line) => line.error_type),
  }
}

/** Matches as the backend does (the agent's search_logs filters). */
function matches(line: LogLine, query: URLSearchParams): boolean {
  const has = (value: string | null, part: string) => (value ?? '').toLowerCase().includes(part.toLowerCase())
  const level = query.get('level'), route = query.get('route'), text = query.get('text')
  const since = query.get('since'), until = query.get('until')
  return (
    (!level || line.level.toUpperCase() === level.toUpperCase()) &&
    (!query.get('service') || line.service === query.get('service')) &&
    (!route || has(line.route, route)) &&
    (!query.get('error_type') || line.error_type === query.get('error_type')) &&
    (!text || has(line.message, text) || has(line.error_type, text)) &&
    (!query.get('trace_id') || line.trace_id === query.get('trace_id')) &&
    (!since || Date.parse(line.ts) >= Date.parse(since)) &&
    (!until || Date.parse(line.ts) <= Date.parse(until))
  )
}

/** A backend with Alice's PayStream project and its logs. `logs` answers the list instead.
 * Returns the log queries it received. */
function fakeBackend({ lines = LINES, logs = undefined as Response | undefined } = {}) {
  const queries: URLSearchParams[] = []
  backend({
    cookie: true,
    projects: [project('p1', 'PayStream')],
    handle: (route, _body, query) => {
      switch (route) {
        case 'GET /projects/p1/logs/facets':
          return json(200, facets(lines))
        case 'GET /projects/p1/logs': {
          queries.push(query)
          if (logs) return logs
          const matching = lines.filter((line) => matches(line, query)).sort((a, b) => b.ts.localeCompare(a.ts))
          const limit = Number(query.get('limit')), offset = Number(query.get('offset') ?? 0)
          return json(200, { items: matching.slice(offset, offset + limit), total: matching.length, limit, offset })
        }
      }
      return undefined
    },
  })
  return queries
}

beforeEach(() => {
  stubMatchMedia()
  localStorage.setItem('roottrace.project', 'p1')
})

afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const table = () => within(screen.getByRole('table', { name: 'Log lines' }))
/** The table's rows, as their cells' text. */
const rows = () =>
  table()
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent))
const messages = () => rows().map((cells) => cells[6])
const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement | HTMLSelectElement

/** Opens the page, and waits for the first page of lines. */
async function openLogs(path = '/logs') {
  const router = renderApp(path)
  await screen.findByRole('heading', { name: 'Logs' })
  await screen.findByRole('table', { name: 'Log lines' })
  return router
}

describe('logs page', () => {
  it('lists the newest lines first, with the time the logs cover', async () => {
    const queries = fakeBackend()
    await openLogs()

    expect(screen.getByText('5 lines, from 2026-10-06 14:19:50 to 2026-10-06 14:26:28')).toBeTruthy()
    expect(messages()).toEqual([
      'payment retried',
      TOO_MANY,
      'RequestValidationErrorrequest validation failed: missing field', // its type, then the message
      'Deploydeploy started: release v1.4.8 (rolling, 5 instances)',
      'request completed',
    ])
    expect(rows()[1]).toEqual([
      '2026-10-06 14:26:27',
      'ERROR',
      'api-3',
      '/payments',
      '500',
      '5,003 ms',
      TOO_MANY, // starts with its type: not repeated
      '5a07979f',
    ])
    expect(rows()[3].slice(4, 6)).toEqual(['', '']) // a deploy has no status or latency
    expect(screen.queryByLabelText('Service')).toBeNull() // only one service
    expect([...queries[0]]).toEqual([['limit', '100'], ['offset', '0']]) // no filters
  })

  it('filters by the values the logs hold', async () => {
    const user = userEvent.setup()
    const queries = fakeBackend()
    const router = await openLogs()

    const level = field('Level') as HTMLSelectElement
    expect([...level.options].map((option) => option.text)).toEqual([
      'Any level',
      'ERROR (1)',
      'INFO (3)',
      'WARN (1)',
    ])
    await user.selectOptions(level, 'WARN')

    await vi.waitFor(() => expect(messages()).toEqual(['RequestValidationErrorrequest validation failed: missing field']))
    expect(here(router)).toBe('/logs?level=WARN')

    await user.selectOptions(level, '')
    await user.selectOptions(field('Error type'), 'OperationalError')

    await vi.waitFor(() => expect(messages()).toEqual([TOO_MANY]))
    expect(here(router)).toBe('/logs?error_type=OperationalError')
    expect(queries.at(-1)?.get('error_type')).toBe('OperationalError')
  })

  it('searches routes and messages once typing pauses', async () => {
    const user = userEvent.setup()
    const queries = fakeBackend()
    const router = await openLogs()

    await user.type(field('Route'), '/web')

    await vi.waitFor(() => expect(rows()).toHaveLength(1))
    expect(queries.filter((query) => query.has('route')).map((query) => query.get('route'))).toEqual(['/web'])
    expect(here(router)).toBe('/logs?route=%2Fweb')

    await user.clear(field('Route'))
    await user.type(field('Message'), 'RETRIED')

    await vi.waitFor(() => expect(messages()).toEqual(['payment retried']))
    expect(here(router)).toBe('/logs?text=RETRIED')
  })

  it('opens on the filters in a link, with its times in UTC', async () => {
    const queries = fakeBackend()
    const router = await openLogs('/logs?route=/payments&since=2026-10-06T14:20:00Z&until=2026-10-06T14:26:27Z')

    expect(messages()).toEqual([TOO_MANY])
    expect(field('Route').value).toBe('/payments')
    expect(field('From (UTC)').value).toBe('2026-10-06T14:20') // inputs leave out :00 seconds
    expect(field('To (UTC)').value).toBe('2026-10-06T14:26:27')
    expect(queries[0].get('since')).toBe('2026-10-06T14:20:00Z')

    fireEvent.change(field('From (UTC)'), { target: { value: '2026-10-06T14:00' } })

    await vi.waitFor(() => expect(messages()).toEqual([TOO_MANY, 'request completed']))
    expect(new URLSearchParams(router.state.location.search).get('since')).toBe('2026-10-06T14:00:00Z')
  })

  it('shows every line of a trace, then all of them again', async () => {
    const user = userEvent.setup()
    fakeBackend()
    const router = await openLogs('/logs?level=ERROR')

    await user.click(table().getByRole('button', { name: '5a07979f' }))

    await vi.waitFor(() => expect(messages()).toEqual(['payment retried', TOO_MANY]))
    expect(here(router)).toBe('/logs?trace_id=5a07979ff653e026') // whatever else was filtered
    expect(field('Level').value).toBe('')
    expect(screen.getByText('trace 5a07979ff653e026')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Stop showing only this trace' }))

    await vi.waitFor(() => expect(rows()).toHaveLength(5))
    expect(here(router)).toBe('/logs')
  })

  it('clears every filter, the typed ones too', async () => {
    const user = userEvent.setup()
    fakeBackend()
    const router = renderApp('/logs?route=/pay&text=nothing%20like%20this&level=INFO')
    expect(await screen.findByText('No lines match these filters.')).toBeTruthy()

    await user.click(screen.getAllByRole('button', { name: 'Clear filters' })[0])

    await vi.waitFor(() => expect(rows()).toHaveLength(5))
    expect(field('Route').value).toBe('')
    expect(field('Message').value).toBe('')
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DELAY_MS + 100))
    expect(here(router)).toBe('/logs') // the boxes didn't put their old text back
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull()
  })

  it('pages through many lines', async () => {
    const user = userEvent.setup()
    const many = Array.from({ length: 250 }, (_, n) =>
      logLine(n + 1, new Date(Date.UTC(2026, 9, 6, 14, 0, n)).toISOString(), { message: `line ${n + 1}` }),
    )
    const queries = fakeBackend({ lines: many })
    await openLogs()

    expect(screen.getByText('1–100 of 250')).toBeTruthy()
    expect(messages()[0]).toBe('line 250')

    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('101–200 of 250')).toBeTruthy()
    expect(queries.at(-1)?.get('offset')).toBe('100')
    expect(messages()[0]).toBe('line 150')
  })

  it("says when From is after To, without asking the backend", async () => {
    const queries = fakeBackend()
    renderApp('/logs?since=2026-10-06T15:00:00Z&until=2026-10-06T14:00:00Z')

    expect((await screen.findByRole('alert')).textContent).toBe('“From” is after “To”, so no line can match.')
    expect(queries).toEqual([])
    expect(screen.queryByRole('table')).toBeNull()
  })

  it("shows the backend's reason for a query it refuses", async () => {
    const refused = json(422, {
      detail: 'Request validation failed',
      code: 'validation_error',
      errors: [{ field: 'query.since', message: 'Input should be a valid datetime or date, input is too short' }],
    })
    fakeBackend({ logs: refused })
    renderApp('/logs?since=yesterday')

    expect((await screen.findByRole('alert')).textContent).toBe(
      'since: Input should be a valid datetime or date, input is too short',
    )
  })

  it('says when the project has no logs', async () => {
    const queries = fakeBackend({ lines: [] })
    renderApp('/logs')

    expect(await screen.findByText('No logs yet')).toBeTruthy()
    expect(queries).toEqual([])
  })
})
