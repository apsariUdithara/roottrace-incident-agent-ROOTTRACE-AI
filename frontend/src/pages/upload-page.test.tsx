// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import type { Schemas } from '@/api/types'
import { queryClient } from '@/lib/query-client'
import { backend, here, json, project, renderApp, stubMatchMedia } from '@/test/helpers'

type Mapping = Record<string, string>
type Sent = { route: string; kind: string; size: number; mapping: Mapping | null }

const LOG_FIELDS: Schemas['UploadField'][] = [
  { name: 'ts', required: true, description: 'When it was logged' },
  { name: 'level', required: true, description: 'As logged: INFO, WARN, ERROR…' },
  { name: 'message', required: false, description: 'The log message' },
]
const INCIDENT_FIELDS: Schemas['UploadField'][] = [
  { name: 'key', required: true, description: 'e.g. INC-017' },
  { name: 'title', required: true, description: 'A one-line summary' },
]
const ACCESS_LOG = 'timestamp,lvl,msg,region\n2026-10-06T14:26:27Z,ERROR,too many connections,eu-1\n'

/** What the backend would say about the file: its guess finds ts and message, but not level. */
function preview(kind: Schemas['UploadKind'], filename: string, mapping: Mapping | null): Schemas['UploadPreview'] {
  const used = mapping ?? (kind === 'logs' ? { ts: 'timestamp', message: 'msg' } : { title: 'msg' })
  const fields = kind === 'logs' ? LOG_FIELDS : INCIDENT_FIELDS
  const missing = fields.filter((field) => field.required && !used[field.name]).map((field) => field.name)
  return {
    kind,
    filename,
    format: 'csv',
    rows: 3,
    columns: ['timestamp', 'lvl', 'msg', 'region'],
    sample: [{ timestamp: '2026-10-06T14:26:27Z', lvl: 'ERROR', msg: 'too many connections', region: 'eu-1' }],
    fields,
    mapping: used,
    mapping_error: missing.length ? `Choose the column that holds ${missing.join(' and ')}.` : null,
    result: missing.length
      ? null
      : { kind, filename, imported: 2, skipped: 0, failed: 1, errors: [{ row: 3, message: 'ts: not a time' }] },
  }
}

/** A backend with Alice's project. `previews` and `uploads` answer instead of the stand-in.
 * Returns what each upload request sent. jsdom's File loses its name on the way through Node's
 * fetch (a browser keeps it), so the stand-in calls the file `filename`. */
function fakeBackend({
  filename = 'access.csv',
  previews = undefined as Response | undefined,
  uploads = undefined as Response | undefined,
} = {}) {
  const sent: Sent[] = []
  backend({
    cookie: true,
    projects: [project('p1', 'PayStream')],
    handle: (route, body) => {
      if (route !== 'POST /projects/p1/uploads/preview' && route !== 'POST /projects/p1/uploads') return undefined
      const form = body as FormData
      const file = form.get('file') as File
      const mapping = form.get('mapping') ? (JSON.parse(form.get('mapping') as string) as Mapping) : null
      sent.push({ route, kind: form.get('kind') as string, size: file.size, mapping })
      const kind = form.get('kind') as Schemas['UploadKind']
      if (route.endsWith('/preview')) return previews ?? json(200, preview(kind, filename, mapping))
      return uploads ?? json(201, preview(kind, filename, mapping).result)
    },
  })
  return sent
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

const status = () => screen.getByRole('status')
const importButton = () => screen.getByRole('button', { name: /^Import/ }) as HTMLButtonElement

async function openUpload(path = '/upload') {
  const router = renderApp(path)
  await screen.findByRole('heading', { name: 'Upload logs or incidents' })
  await screen.findByText('Choose a file') // once the project has loaded
  return router
}

async function chooseFile(user: ReturnType<typeof userEvent.setup>, name = 'access.csv', content = ACCESS_LOG) {
  await user.upload(screen.getByLabelText(/^File/), new File([content], name, { type: 'text/csv' }))
}

describe('upload page', () => {
  it('checks the file, lets its columns be matched, then imports it', async () => {
    const user = userEvent.setup()
    const sent = fakeBackend()
    await openUpload()

    await chooseFile(user)

    expect(await screen.findByText('Choose the column that holds level.')).toBeTruthy()
    expect(sent).toEqual([
      { route: 'POST /projects/p1/uploads/preview', kind: 'logs', size: ACCESS_LOG.length, mapping: null },
    ])
    expect(screen.getByText('3 rows, read as CSV.', { exact: false })).toBeTruthy()
    expect((screen.getByLabelText('Column for ts') as HTMLSelectElement).value).toBe('timestamp')
    expect(importButton().disabled).toBe(true)

    await user.selectOptions(screen.getByLabelText('Column for level'), 'lvl')

    expect(await within(status()).findByText('2 log lines will be imported.')).toBeTruthy()
    expect(sent.at(-1)?.mapping).toEqual({ ts: 'timestamp', message: 'msg', level: 'lvl' })
    expect(status().textContent).toContain('1 row with a problem will be left out:')
    expect(within(status()).getByRole('listitem').textContent).toBe('Row 3: ts: not a time')
    expect(screen.getByText('Kept with each line as extra fields: region')).toBeTruthy()
    const level = screen.getByLabelText('Column for level').closest('tr')!
    expect(within(level).getByText('ERROR')).toBeTruthy() // its value in the first row

    await user.click(screen.getByRole('button', { name: 'Import 2 log lines' }))

    expect(await screen.findByText('Imported 2 log lines from access.csv.', { exact: false })).toBeTruthy()
    expect(sent.at(-1)).toEqual({
      route: 'POST /projects/p1/uploads',
      kind: 'logs',
      size: ACCESS_LOG.length,
      mapping: { ts: 'timestamp', message: 'msg', level: 'lvl' }, // as checked
    })
    expect(screen.getByRole('link', { name: 'Open the logs' }).getAttribute('href')).toBe('/logs')

    await user.click(screen.getByRole('button', { name: 'Upload another file' }))

    expect(screen.getByText('Choose a file')).toBeTruthy()
    expect((screen.getByLabelText(/^File/) as HTMLInputElement).files).toHaveLength(0)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('opens for incidents from a link, and guesses again when that changes', async () => {
    const user = userEvent.setup()
    const sent = fakeBackend({ filename: 'history.csv' })
    const router = await openUpload('/upload?kind=incidents')

    expect((screen.getByLabelText('Incidents') as HTMLInputElement).checked).toBe(true)
    expect(screen.getByText(/never overwritten/)).toBeTruthy()
    await chooseFile(user, 'history.csv')
    expect(await screen.findByText('Choose the column that holds key.')).toBeTruthy()
    await user.selectOptions(screen.getByLabelText('Column for key'), 'timestamp')
    await within(status()).findByText('2 incidents will be imported.')

    await user.click(screen.getByLabelText('Logs'))

    expect(await screen.findByText('Choose the column that holds level.')).toBeTruthy()
    expect(sent.at(-1)).toMatchObject({ kind: 'logs', mapping: null })
    expect(screen.getByText('history.csv')).toBeTruthy()
    expect(here(router)).toBe('/upload?kind=logs')
  })

  it("says why a file can't be read", async () => {
    const user = userEvent.setup()
    fakeBackend({
      previews: json(422, { detail: "This isn't valid YAML: expected ',' or ']' (line 2).", code: 'unprocessable_content' }),
    })
    await openUpload()

    await chooseFile(user, 'broken.yaml', '- ts: [1\n')

    expect((await screen.findByRole('alert')).textContent).toBe("This isn't valid YAML: expected ',' or ']' (line 2).")
    expect(screen.queryByRole('button', { name: /^Import/ })).toBeNull()
  })

  it("shows the backend's reason when the import fails, and stays on the check", async () => {
    const user = userEvent.setup()
    fakeBackend({
      uploads: json(502, {
        detail: "Couldn't index the incidents for search, so none were saved. Please try again in a moment.",
        code: 'bad_gateway',
      }),
    })
    await openUpload('/upload?kind=incidents')
    await chooseFile(user, 'history.csv')
    await user.selectOptions(await screen.findByLabelText('Column for key'), 'timestamp')

    await user.click(await screen.findByRole('button', { name: 'Import 2 incidents' }))

    expect((await screen.findByRole('alert')).textContent).toBe(
      "Couldn't index the incidents for search, so none were saved. Please try again in a moment.",
    )
    expect(importButton().disabled).toBe(false) // it can be tried again
  })
})
