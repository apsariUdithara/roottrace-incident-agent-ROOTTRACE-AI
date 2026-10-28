// @vitest-environment jsdom
import { act, cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import type { Schemas } from '@/api/types'
import { queryClient } from '@/lib/query-client'
import { POLL_MS } from '@/lib/repositories'
import { backend, here, json, project, renderApp, stubMatchMedia } from '@/test/helpers'

type Repository = Schemas['RepositoryOut']
type Item = Schemas['KnowledgeItem']

const PAYSTREAM = project('p1', 'PayStream')
const SHA = 'f89f6df0a1b2c3d4e5f60718293a4b5c6d7e8f90'

function repository(fields: Partial<Repository> = {}): Repository {
  return {
    id: 'r1',
    provider: 'github',
    url: 'https://github.com/acme/paystream',
    default_branch: 'v1.4.8',
    has_token: false,
    setup_command: null,
    test_command: 'pytest -q',
    sandbox_status: 'unknown',
    ingest_status: 'indexed',
    ingest_error: null,
    last_indexed_sha: SHA,
    last_indexed_at: '2026-10-28T09:00:00Z',
    created_at: '2026-10-27T10:00:00Z',
    ...fields,
  }
}

function item(source_type: Item['source_type'], path: string, title: string | null = null): Item {
  return {
    id: `${source_type}:${path}`,
    source_type,
    path,
    title: title ?? path.split('/').at(-1)!,
    commit_sha: source_type === 'doc' || source_type === 'code' ? SHA : null,
    chunk_count: 3,
    source_ref: `${source_type}:${path}`,
    created_at: '2026-10-28T09:00:00Z',
  }
}

const ITEMS = [
  item('doc', 'docs/pool_sizing.md'),
  item('doc', 'README.md'),
  item('code', 'app/db.py'),
  item('incident', 'INC-017', 'Payments 500s: pool exhausted'),
  item('issue', 'issue:42', 'Checkout fails after deploy'),
]

/** A backend with Alice's PayStream project and its repository. A re-index moves through
 * `afterIngest`, one state per poll. Returns the knowledge queries it received. */
function fakeBackend({
  repositories = [repository()] as Repository[],
  items = ITEMS,
  afterIngest = [] as Repository[],
  ingest = undefined as Response | undefined,
} = {}) {
  const queries: URLSearchParams[] = []
  let current = repositories
  const states = [...afterIngest]
  const calls = backend({
    cookie: true,
    projects: [PAYSTREAM],
    handle: (route, _body, query) => {
      switch (route) {
        case 'GET /projects/p1/repositories':
          if (current[0]?.ingest_status === 'queued' || current[0]?.ingest_status === 'running') {
            current = [states.shift() ?? current[0]]
          }
          return json(200, current)
        case 'POST /projects/p1/ingest':
          if (ingest) return ingest
          current = [{ ...current[0], ingest_status: 'queued', ingest_error: null }]
          return json(202, current)
        case 'GET /projects/p1/knowledge': {
          queries.push(query)
          const q = (query.get('q') ?? '').toLowerCase()
          const matching = items.filter(
            (i) =>
              i.source_type === (query.get('type') ?? i.source_type) &&
              (i.path.toLowerCase().includes(q) || (i.title ?? '').toLowerCase().includes(q)),
          )
          const limit = Number(query.get('limit')), offset = Number(query.get('offset'))
          const page = matching.slice(offset, offset + limit)
          return json(200, { items: page, total: matching.length, limit, offset })
        }
      }
      return undefined
    },
  })
  return { queries, calls }
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
  vi.useRealTimers()
})

const list = () => within(screen.getByRole('list', { name: 'Indexed items' }))
const titles = () => list().getAllByRole('listitem').map((row) => row.querySelector('span')?.textContent)
const status = () => screen.getByRole('status')

/** Opens the page, and waits for the first page of items (or, with none, the tabs). */
async function openKnowledge(path = '/knowledge', { withItems = true } = {}) {
  const router = renderApp(path)
  await screen.findByRole('heading', { name: 'Knowledge' })
  await screen.findByRole(withItems ? 'list' : 'tablist', withItems ? { name: 'Indexed items' } : {})
  return router
}

describe('knowledge page', () => {
  it("shows the repository's status and its docs, and switches tabs", async () => {
    const user = userEvent.setup()
    const { queries } = fakeBackend()
    const router = await openKnowledge()

    expect(screen.getByText('acme/paystream')).toBeTruthy()
    expect(within(status()).getByText('Indexed')).toBeTruthy()
    expect(status().textContent).toContain('At commit f89f6df')
    expect(titles()).toEqual(['docs/pool_sizing.md', 'README.md'])
    expect(queries[0].get('type')).toBe('doc')

    await user.click(screen.getByRole('tab', { name: 'Incidents' }))

    expect(await list().findByText('Payments 500s: pool exhausted')).toBeTruthy()
    expect(list().getByText('INC-017')).toBeTruthy() // the key under its title
    expect(here(router)).toBe('/knowledge?type=incident')
    expect(screen.getByRole('tab', { name: 'Incidents' }).getAttribute('aria-selected')).toBe('true')
    const upload = screen.getByRole('link', { name: 'Upload incident history' })
    expect(upload.getAttribute('href')).toBe('/upload?kind=incidents')
  })

  it('opens on the tab and search in the link', async () => {
    fakeBackend()
    await openKnowledge('/knowledge?type=code&q=db')

    expect(titles()).toEqual(['app/db.py'])
    expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('db')
  })

  it('searches once typing pauses, and says when nothing matches', async () => {
    const user = userEvent.setup()
    const { queries } = fakeBackend()
    const router = await openKnowledge()

    await user.type(screen.getByRole('searchbox', { name: 'Search titles and paths' }), 'pool')

    await vi.waitFor(() => expect(titles()).toEqual(['docs/pool_sizing.md']))
    expect(queries.filter((query) => query.has('q')).map((query) => query.get('q'))).toEqual([
      'pool',
    ]) // one search, not one per key
    expect(here(router)).toBe('/knowledge?q=pool')

    await user.clear(screen.getByRole('searchbox'))
    await user.type(screen.getByRole('searchbox'), 'zzz')
    expect(await screen.findByText('Nothing matches “zzz”.')).toBeTruthy()
  })

  it('pages through a long list', async () => {
    const user = userEvent.setup()
    const code = Array.from({ length: 120 }, (_, n) => item('code', `app/m${String(n).padStart(3, '0')}.py`))
    const { queries } = fakeBackend({ items: code })
    await openKnowledge('/knowledge?type=code')

    expect(screen.getByText('1–50 of 120')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('51–100 of 120')).toBeTruthy()
    expect(queries.at(-1)?.get('offset')).toBe('50')
    expect(titles()[0]).toBe('app/m050.py')
  })

  it('re-indexes, follows the run, and reloads the list when it ends', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const newSha = 'a3f9c21d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7'
    const { queries, calls } = fakeBackend({
      afterIngest: [
        repository({ ingest_status: 'running' }),
        repository({ last_indexed_sha: newSha, last_indexed_at: '2026-10-28T10:00:00Z' }),
      ],
    })
    await openKnowledge()
    const loads = queries.length

    await user.click(screen.getByRole('button', { name: 'Re-index' }))

    expect(await within(status()).findByText('Waiting to index')).toBeTruthy()
    expect(calls).toContain('POST /projects/p1/ingest')
    expect((screen.getByRole('button', { name: 'Re-index' }) as HTMLButtonElement).disabled).toBe(true)
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS))
    expect(await within(status()).findByText('Indexing…')).toBeTruthy()
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS))
    expect(await within(status()).findByText('Indexed')).toBeTruthy()
    expect(status().textContent).toContain('At commit a3f9c21')
    expect((screen.getByRole('button', { name: 'Re-index' }) as HTMLButtonElement).disabled).toBe(false)
    await vi.waitFor(() => expect(queries.length).toBe(loads + 1)) // the list, reloaded once
  })

  it('says why indexing failed, and what the agent still searches', async () => {
    fakeBackend({
      repositories: [
        repository({ ingest_status: 'failed', ingest_error: "The branch main wasn't found." }),
      ],
    })
    await openKnowledge()

    expect(within(status()).getByText('Indexing failed')).toBeTruthy()
    expect(status().textContent).toContain("The branch main wasn't found.")
    expect(status().textContent).toContain('The agent still searches commit f89f6df')
    expect((screen.getByRole('button', { name: 'Re-index' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('offers to index a repository that never was', async () => {
    fakeBackend({
      repositories: [
        repository({ ingest_status: 'not_indexed', last_indexed_sha: null, last_indexed_at: null }),
      ],
      items: [],
    })
    await openKnowledge('/knowledge', { withItems: false })

    expect(screen.getByRole('button', { name: 'Index now' })).toBeTruthy()
    expect(await screen.findByText('Nothing indexed yet. Index the repository above.')).toBeTruthy()
  })

  it("shows the backend's reason when indexing can't start", async () => {
    const user = userEvent.setup()
    fakeBackend({
      ingest: json(409, { detail: 'Connect a repository to this project first', code: 'conflict' }),
    })
    await openKnowledge()

    await user.click(screen.getByRole('button', { name: 'Re-index' }))

    expect((await screen.findByRole('alert')).textContent).toBe(
      'Connect a repository to this project first',
    )
  })

  it('without a repository, links to where to connect one', async () => {
    fakeBackend({ repositories: [], items: [] })
    await openKnowledge('/knowledge', { withItems: false })

    const link = screen.getByRole('link', { name: 'Connect a GitHub repository' })
    expect(link.getAttribute('href')).toBe('/projects/p1')
    expect(screen.queryByRole('button', { name: /index/i })).toBeNull()
  })

  it('without a project, links to creating one', async () => {
    backend({ cookie: true, projects: [] })
    renderApp('/knowledge')

    const hint = await screen.findByText(/first, then connect its repository/)
    expect(within(hint).getByRole('link', { name: 'Create a project' }).getAttribute('href')).toBe(
      '/projects',
    )
    expect(screen.queryByRole('tab')).toBeNull()
  })
})
