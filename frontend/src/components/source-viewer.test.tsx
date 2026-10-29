// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SourceViewerProvider } from '@/components/source-viewer'
import { queryClient } from '@/lib/query-client'
import { useSourceViewer } from '@/lib/source-viewer'
import { backend, json } from '@/test/helpers'
// Loaded up front: the viewer imports it lazily, and a cold start can outlast findBy's wait.
import '@/components/doc-markdown'

const SHA = '6c4a9a3f0000000000000000000000000000beef'

const SOURCES: Record<string, unknown> = {
  'src/config.py L2-3 @ v1.4.8': {
    kind: 'file',
    ref: 'src/config.py L2-3 @ v1.4.8',
    path: 'src/config.py',
    sha: SHA,
    language: 'python',
    content: 'import os\nMAX = 100\nPOOL = 100\nTIMEOUT = 5\n',
    start_line: 2,
    end_line: 3,
  },
  'commit 5b0bb55c90': {
    kind: 'commit',
    ref: 'commit 5b0bb55c90',
    sha: '5b0bb55c90aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    message: 'perf: raise the pool to 100\n\nMore traffic expected.',
    author: 'Maya',
    committed_at: '2026-10-06T14:05:00+00:00',
    files: ['src/config.py'],
    diff: '--- a/src/config.py\n+++ b/src/config.py\n@@ -1,2 +1,2 @@\n-POOL = 20\n+POOL = 100\n',
  },
  'docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3': {
    kind: 'doc',
    ref: 'docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3',
    path: 'docs/database.md',
    section: 'connection-pooling',
    title: 'Database',
    markdown: '# Database\n\nIntro.\n\n## Connection pooling\n\nKeep it at **20** or less.\n',
  },
  'log trace_id=t-1': {
    kind: 'log',
    ref: 'log trace_id=t-1',
    trace_id: 't-1',
    entries: [
      {
        id: 1,
        ts: '2026-10-06T14:26:27Z',
        level: 'ERROR',
        service: 'api',
        instance: 'api-3',
        route: '/payments',
        status: 500,
        latency_ms: 5003,
        message: 'OperationalError: too many connections',
        error_type: 'OperationalError',
        trace_id: 't-1',
        attrs: {},
      },
    ],
  },
  'issue:41': {
    kind: 'issue',
    ref: 'issue:41',
    number: 41,
    title: 'Checkout returns 500',
    body: 'Since **this morning**.',
    state: 'open',
    url: 'https://github.com/acme/paystream/issues/41',
  },
  'memory:m1': {
    kind: 'memory',
    ref: 'memory:m1',
    memory: {
      id: 'm1',
      category: 'project_fact',
      content: 'Payments use PostgreSQL',
      source_ref: 'investigation:abc',
      source_investigation_id: null,
      created_by: 'agent',
      status: 'active',
      created_at: '2026-10-06T15:00:00Z',
      updated_at: '2026-10-06T15:00:00Z',
    },
  },
}

/** A page with one button per ref, in an app with the source viewer; the backend knows SOURCES
 * in project p1. Returns the refs it was asked for, and the router. */
function openPage(refs: string[]) {
  const asked: string[] = []
  backend({
    handle: (route, _body, query) => {
      if (route !== 'GET /projects/p1/sources') return undefined
      const ref = query.get('ref')!
      asked.push(ref)
      return ref in SOURCES
        ? json(200, SOURCES[ref])
        : json(404, { detail: `${ref} doesn't exist at 6c4a9a3`, code: 'not_found' })
    },
  })
  function Page() {
    const openSource = useSourceViewer()
    return refs.map((ref) => (
      <button key={ref} type="button" onClick={() => openSource('p1', ref)}>
        {ref}
      </button>
    ))
  }
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <SourceViewerProvider>
            <Page />
          </SourceViewerProvider>
        ),
      },
      { path: '/logs', element: <p>Logs page</p> },
    ],
    { initialEntries: ['/'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { asked, router }
}

async function open(ref: string) {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: ref }))
  return { user, viewer: within(await screen.findByRole('dialog')) }
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  queryClient.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('source viewer', () => {
  it('shows a file with its cited lines marked, and scrolls to them', async () => {
    openPage(['src/config.py L2-3 @ v1.4.8'])
    const { viewer } = await open('src/config.py L2-3 @ v1.4.8')

    const table = await viewer.findByRole('table', { name: 'src/config.py' })
    const cited = [...table.querySelectorAll('tr[data-cited]')].map((row) => row.textContent)
    expect(cited).toEqual(['2MAX = 100', '3POOL = 100'])
    expect(table.querySelectorAll('tr')).toHaveLength(4) // no empty line for the last newline
    expect(viewer.getByText('· cited: lines 2–3')).toBeTruthy()
    expect(viewer.getByText('at 6c4a9a3')).toBeTruthy()
    expect(viewer.getByText('config.py L2-3')).toBeTruthy() // the title: the ref's short form
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
  })

  it('shows a commit: message, author, files and the diff', async () => {
    openPage(['commit 5b0bb55c90'])
    const { viewer } = await open('commit 5b0bb55c90')

    expect(await viewer.findByRole('heading', { name: 'perf: raise the pool to 100' })).toBeTruthy()
    expect(viewer.getByText('More traffic expected.')).toBeTruthy()
    expect(viewer.getByText('· Maya')).toBeTruthy()
    expect(viewer.getByText('· 2026-10-06 14:05:00 UTC')).toBeTruthy()
    const diff = viewer.getByLabelText('Diff')
    expect(within(diff).getByText('+POOL = 100').className).toContain('text-primary')
    expect(within(diff).getByText('-POOL = 20').className).toContain('text-destructive')
  })

  it('shows a doc as Markdown with the cited section marked', async () => {
    openPage(['docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3'])
    const { viewer } = await open('docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3')

    const section = await viewer.findByRole('heading', { name: 'Connection pooling' })
    await waitFor(() => expect(section.getAttribute('data-cited')).toBe('true'))
    expect(viewer.getByRole('heading', { name: 'Database' }).getAttribute('data-cited')).toBeNull()
    expect(viewer.getByText('20').tagName).toBe('STRONG')
  })

  it("shows a trace's log lines, and opens the trace in Logs", async () => {
    const { router } = openPage(['log trace_id=t-1'])
    const { user, viewer } = await open('log trace_id=t-1')

    const row = within(await viewer.findByRole('table', { name: 'Log lines' })).getAllByRole('row')[1]
    expect(row.textContent).toContain('2026-10-06 14:26:27')
    expect(row.textContent).toContain('OperationalError: too many connections')
    expect(viewer.getByText('1 line of trace t-1')).toBeTruthy()

    await user.click(viewer.getByRole('link', { name: 'Open in Logs' }))

    expect(router.state.location.pathname + router.state.location.search).toBe('/logs?trace_id=t-1')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull()) // the drawer closed
    expect(localStorage.getItem('roottrace.project')).toBe('p1') // Logs shows that project
  })

  it('shows an issue and a memory (chat-page.test.tsx opens an incident)', async () => {
    openPage(['issue:41', 'memory:m1'])

    const { user, viewer: issue } = await open('issue:41')
    expect(await issue.findByRole('heading', { name: '#41 Checkout returns 500' })).toBeTruthy()
    expect(issue.getByRole('link', { name: 'Open on GitHub' }).getAttribute('href')).toBe(
      'https://github.com/acme/paystream/issues/41',
    )
    expect((await issue.findByText('this morning')).tagName).toBe('STRONG')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const { viewer: memory } = await open('memory:m1')
    expect(await memory.findByText('Payments use PostgreSQL')).toBeTruthy()
    expect(memory.getByText('Project fact')).toBeTruthy()
    expect(memory.getByText('Written by the agent: check it before relying on it')).toBeTruthy()
  })

  it("says why a ref doesn't open, in the backend's words", async () => {
    const { asked } = openPage(['src/missing.py L1-2 @ v1.4.8'])
    const { viewer } = await open('src/missing.py L1-2 @ v1.4.8')

    expect((await viewer.findByRole('alert')).textContent).toBe(
      "src/missing.py L1-2 @ v1.4.8 doesn't exist at 6c4a9a3",
    )
    expect(asked).toEqual(['src/missing.py L1-2 @ v1.4.8']) // a 404 isn't retried
  })

  it('closes with Escape, and opens the next ref fresh', async () => {
    openPage(['commit 5b0bb55c90', 'issue:41'])
    const { user, viewer } = await open('commit 5b0bb55c90')
    await viewer.findByRole('heading', { name: 'perf: raise the pool to 100' })

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const { viewer: next } = await open('issue:41')

    expect(await next.findByRole('heading', { name: '#41 Checkout returns 500' })).toBeTruthy()
    expect(next.queryByText('perf: raise the pool to 100')).toBeNull()
  })
})
