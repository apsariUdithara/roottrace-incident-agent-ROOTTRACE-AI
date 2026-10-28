// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import type { Schemas } from '@/api/types'
import { queryClient } from '@/lib/query-client'
import { backend, here, json, project, renderApp, stubMatchMedia } from '@/test/helpers'

type Project = Schemas['ProjectOut']
type Repository = Schemas['RepositoryOut']

const PAYSTREAM = project('p1', 'PayStream')
const CHECKOUT = project('p2', 'Checkout')
const REPO_URL = 'https://github.com/acme/payments'

function repository(fields: Partial<Repository> = {}): Repository {
  return {
    id: 'r1',
    provider: 'github',
    url: REPO_URL,
    default_branch: 'main',
    has_token: false,
    setup_command: null,
    test_command: 'pytest -q',
    sandbox_status: 'unknown',
    ingest_status: 'not_indexed',
    ingest_error: null,
    last_indexed_sha: null,
    last_indexed_at: null,
    created_at: '2026-10-28T10:00:00Z',
    ...fields,
  }
}

/** A backend that keeps Alice's projects and repositories (by project id) as they change.
 * Returns the bodies sent to the routes it handles, e.g. ["PATCH /projects/p1", {...}]. */
function fakeBackend({
  projects = [PAYSTREAM] as Project[],
  repositories = {} as Record<string, Repository[]>,
} = {}) {
  projects = projects.map((p) => ({ ...p })) // edited in place below
  const sent: [string, unknown][] = []
  const calls = backend({
    cookie: true,
    projects,
    handle: (route, body) => {
      sent.push([route, body])
      const [method, path] = route.split(' ')
      const [, , projectId, , repositoryId] = path.split('/') // /projects/{id}/repositories/{id}
      const found = projects.find((p) => p.id === projectId)
      if (route === 'POST /projects') {
        const { name, description } = body as Schemas['ProjectCreate']
        if (projects.some((p) => p.name === name)) {
          return json(409, { detail: 'You already have a project with this name', code: 'conflict' })
        }
        const created = { ...project(`p${projects.length + 1}`, name), description }
        projects.unshift(created)
        return json(201, created)
      }
      if (!found) return undefined
      if (route === `PATCH /projects/${projectId}`) {
        return json(200, Object.assign(found, body))
      }
      if (route === `GET /projects/${projectId}/repositories`) {
        return json(200, repositories[projectId] ?? [])
      }
      if (route === `POST /projects/${projectId}/repositories`) {
        const { token, ...fields } = body as Schemas['RepositoryCreate']
        repositories[projectId] = [repository({ ...fields, has_token: Boolean(token) })]
        return json(201, repositories[projectId][0])
      }
      const [current] = repositories[projectId] ?? []
      if (!current || current.id !== repositoryId) return undefined
      if (method === 'PATCH') {
        const { token, ...fields } = body as Schemas['RepositoryUpdate']
        const hasToken = token === undefined ? current.has_token : token !== null
        repositories[projectId] = [{ ...current, ...fields, has_token: hasToken } as Repository]
        return json(200, repositories[projectId][0])
      }
      if (method === 'DELETE') {
        repositories[projectId] = []
        return new Response(null, { status: 204 })
      }
      return undefined
    },
  })
  return { sent, calls }
}

beforeEach(() => stubMatchMedia())

afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

const projectList = () => within(screen.getByRole('region', { name: 'Your projects' }))
const switcher = () => screen.getByRole('combobox', { name: 'Project' }) as HTMLSelectElement

describe('projects page', () => {
  it('lists your projects, marks the current one, and links to their settings', async () => {
    fakeBackend({ projects: [CHECKOUT, PAYSTREAM] })
    localStorage.setItem('roottrace.project', 'p1')
    renderApp('/projects')
    await screen.findByRole('heading', { name: 'Projects' })

    const paystream = await projectList().findByRole('link', { name: 'PayStream' })

    expect(paystream.getAttribute('href')).toBe('/projects/p1')
    const links = projectList().getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual(['Checkout', 'PayStream'])
    const current = projectList().getByText('Current').closest('[data-slot="card"]')
    expect(current?.textContent).toContain('PayStream')
  })

  it('with no projects, asks you to create one', async () => {
    fakeBackend({ projects: [] })
    renderApp('/projects')

    expect(await screen.findByText('No projects yet. Create your first one below.')).toBeTruthy()
    expect(screen.getByRole('form', { name: 'New project' })).toBeTruthy()
  })

  it('creates a project, makes it the current one, and opens its settings', async () => {
    const user = userEvent.setup()
    const { sent } = fakeBackend()
    localStorage.setItem('roottrace.project', 'p1') // working in PayStream until now
    const router = renderApp('/projects')
    const form = within(await screen.findByRole('form', { name: 'New project' }))

    await user.type(form.getByLabelText('Name'), 'Checkout')
    await user.type(form.getByLabelText('Description (optional)'), 'Card payments')
    await user.click(form.getByRole('button', { name: 'Create project' }))

    expect(await screen.findByRole('heading', { name: 'Checkout' })).toBeTruthy()
    expect(here(router)).toBe('/projects/p2')
    expect(sent[0]).toEqual(['POST /projects', { name: 'Checkout', description: 'Card payments' }])
    expect(switcher().value).toBe('p2')
    expect(await screen.findByRole('form', { name: 'Connect a repository' })).toBeTruthy()
  })

  it("says why a project couldn't be created", async () => {
    const user = userEvent.setup()
    fakeBackend()
    const router = renderApp('/projects')
    const form = within(await screen.findByRole('form', { name: 'New project' }))

    await user.type(form.getByLabelText('Name'), 'PayStream')
    await user.click(form.getByRole('button', { name: 'Create project' }))

    expect((await form.findByRole('alert')).textContent).toBe(
      'You already have a project with this name',
    )
    expect(here(router)).toBe('/projects')
  })
})

describe('project settings', () => {
  async function openSettings(options: Parameters<typeof fakeBackend>[0] = {}) {
    const user = userEvent.setup()
    const backendState = fakeBackend(options)
    renderApp('/projects/p1')
    await screen.findByRole('heading', { name: 'PayStream' })
    return { user, ...backendState }
  }

  it('renames the project, here and in the switcher', async () => {
    const { user, sent } = await openSettings()
    const form = within(screen.getByRole('form', { name: 'Project details' }))

    await user.clear(form.getByLabelText('Name'))
    await user.type(form.getByLabelText('Name'), 'PayStream API')
    await user.click(form.getByRole('button', { name: 'Save' }))

    expect((await form.findByRole('status')).textContent).toBe('Saved.')
    expect(sent).toContainEqual(['PATCH /projects/p1', { name: 'PayStream API', description: '' }])
    expect(screen.getByRole('heading', { name: 'PayStream API' })).toBeTruthy()
    expect(switcher().selectedOptions[0].textContent).toBe('PayStream API')

    await user.type(form.getByLabelText('Description'), 'x')
    expect(form.getByRole('status').textContent).toBe('') // an edit clears "Saved."
  })

  it("says so when the project doesn't exist", async () => {
    fakeBackend()
    renderApp('/projects/p404')

    expect(await screen.findByText('Project not found')).toBeTruthy()
    expect(screen.getByRole('link', { name: '← All projects' }).getAttribute('href')).toBe('/projects')
  })

  it('connects a repository, sending the token once and never showing it', async () => {
    const { user, sent } = await openSettings()
    const form = within(await screen.findByRole('form', { name: 'Connect a repository' }))

    await user.type(form.getByLabelText('GitHub repository'), REPO_URL)
    await user.type(form.getByLabelText('Access token (optional)'), ' ghp_secret123 ')
    await user.clear(form.getByLabelText('Test command'))
    await user.type(form.getByLabelText('Test command'), 'pytest -q tests')
    await user.click(form.getByRole('button', { name: 'Connect repository' }))

    const link = await screen.findByRole('link', { name: 'acme/payments' })
    expect(link.getAttribute('href')).toBe(REPO_URL)
    expect(sent).toContainEqual([
      'POST /projects/p1/repositories',
      {
        provider: 'github',
        url: REPO_URL,
        default_branch: 'main',
        token: 'ghp_secret123',
        setup_command: null, // left empty: auto-detect
        test_command: 'pytest -q tests',
      },
    ])
    expect(screen.getByText('Saved (encrypted)')).toBeTruthy()
    const inputs = [...document.querySelectorAll('input')].map((input) => input.value)
    expect(inputs.join(' ')).not.toContain('ghp_secret123')
    expect(document.body.textContent).not.toContain('ghp_secret123')
  })

  it("shows the backend's reason when connecting fails", async () => {
    const user = userEvent.setup()
    backend({
      cookie: true,
      projects: [PAYSTREAM],
      handle: (route) =>
        route === 'GET /projects/p1/repositories'
          ? json(200, [])
          : json(409, {
              detail: 'This project already has a repository. Disconnect it to connect another one.',
              code: 'conflict',
            }),
    })
    renderApp('/projects/p1')
    const form = within(await screen.findByRole('form', { name: 'Connect a repository' }))

    await user.type(form.getByLabelText('GitHub repository'), REPO_URL)
    await user.click(form.getByRole('button', { name: 'Connect repository' }))

    expect((await form.findByRole('alert')).textContent).toContain('already has a repository')
  })

  it('saves new settings, and keeps the saved token unless a new one is typed', async () => {
    const { user, sent } = await openSettings({
      repositories: { p1: [repository({ has_token: true, setup_command: 'pip install -e .' })] },
    })
    const form = within(await screen.findByRole('form', { name: 'Repository settings' }))
    const save = () => user.click(form.getByRole('button', { name: 'Save' }))

    await user.clear(form.getByLabelText('Setup command (optional)'))
    await save()

    expect((await form.findByRole('status')).textContent).toBe('Saved.')
    expect(sent.at(-1)).toEqual([
      'PATCH /projects/p1/repositories/r1',
      { default_branch: 'main', setup_command: null, test_command: 'pytest -q' },
    ])

    const token = form.getByLabelText('Replace the access token') as HTMLInputElement
    await user.type(token, 'ghp_new')
    await save()
    await form.findByText('Saved.')
    expect(sent.at(-1)?.[1]).toMatchObject({ token: 'ghp_new' })
    expect(token.value).toBe('') // emptied once saved

    await user.click(form.getByRole('button', { name: 'Remove token' }))
    expect(await screen.findByText('None')).toBeTruthy()
    expect(sent.at(-1)?.[1]).toEqual({ token: null })
    expect(form.queryByRole('button', { name: 'Remove token' })).toBeNull()
    expect(form.getByLabelText('Add an access token')).toBeTruthy()
  })

  it('asks before disconnecting, then offers to connect another', async () => {
    const { user, calls } = await openSettings({ repositories: { p1: [repository()] } })

    await user.click(await screen.findByRole('button', { name: 'Disconnect repository' }))
    const confirm = within(screen.getByRole('group', { name: 'Disconnect the repository' }))
    expect(confirm.getByText(/Its docs and code are removed from the knowledge base/)).toBeTruthy()
    expect(document.activeElement?.textContent).toBe('Cancel')
    await user.click(confirm.getByRole('button', { name: 'Cancel' }))
    expect(calls).not.toContain('DELETE /projects/p1/repositories/r1')

    await user.click(screen.getByRole('button', { name: 'Disconnect repository' }))
    await user.click(screen.getByRole('button', { name: 'Disconnect' }))

    expect(await screen.findByRole('form', { name: 'Connect a repository' })).toBeTruthy()
    expect(calls).toContain('DELETE /projects/p1/repositories/r1')
  })

  it('shows whether the tests can run and how the indexing went', async () => {
    await openSettings({
      repositories: {
        p1: [
          repository({
            sandbox_status: 'untestable',
            ingest_status: 'failed',
            ingest_error: 'Could not clone: repository not found',
            last_indexed_sha: 'a3f9c21d0e',
          }),
        ],
      },
    })

    expect(await screen.findByText("Tests can't run")).toBeTruthy()
    expect(screen.getByText(/can't check a fix by running the tests/)).toBeTruthy()
    expect(screen.getByText('Indexing failed')).toBeTruthy()
    expect(screen.getByText('Could not clone: repository not found')).toBeTruthy()
    expect(screen.getByText('a3f9c21').tagName).toBe('CODE')
  })
})
