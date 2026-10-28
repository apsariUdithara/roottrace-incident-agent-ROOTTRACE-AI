// /upload: bring your own logs or incident history into the current project. The file is checked
// first (POST …/uploads/preview, which saves nothing): its columns are matched to our fields, and
// the page shows what importing would do. Then it's imported (POST …/uploads). ?kind=incidents
// opens it for incidents.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { useCurrentProject } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'

type Kind = Schemas['UploadKind']
type Preview = Schemas['UploadPreview']
type Result = Schemas['UploadResult']
type Mapping = Record<string, string>

const KINDS: Record<
  Kind,
  { label: string; hint: string; noun: (n: number) => string; open: { to: string; label: string } }
> = {
  logs: {
    label: 'Logs',
    hint: 'JSONL or CSV (JSON and YAML work too). Lines the project already has are skipped, so a file can be uploaded again.',
    noun: (n) => (n === 1 ? 'log line' : 'log lines'),
    open: { to: '/logs', label: 'Open the logs' },
  },
  incidents: {
    label: 'Incidents',
    hint: 'YAML, JSON or CSV. An incident whose key the project already has is skipped, never overwritten.',
    noun: (n) => (n === 1 ? 'incident' : 'incidents'),
    open: { to: '/knowledge?type=incident', label: 'See them in Knowledge' },
  },
}
const FORMATS: Record<Schemas['UploadFormat'], string> = {
  csv: 'CSV',
  jsonl: 'JSONL',
  json: 'JSON',
  yaml: 'YAML',
}
const ACCEPT = '.csv,.tsv,.jsonl,.ndjson,.json,.yaml,.yml,.log,.txt'

/** The multipart body of an upload. `mapping` null: the backend guesses it from the columns. */
function uploadForm(kind: Kind, file: File, mapping: Mapping | null) {
  const form = new FormData()
  form.append('kind', kind)
  form.append('file', file, file.name)
  if (mapping) form.append('mapping', JSON.stringify(mapping))
  // The generated type says `file: string` (OpenAPI's "binary"); the form carries the File.
  return { body: { kind, file: file.name }, bodySerializer: () => form }
}

export function UploadPage() {
  const { project, isPending, isError } = useCurrentProject()

  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <PageHeader
        title="Upload logs or incidents"
        description="Bring your own data into this project: log lines for the agent's log search, and past incidents for its search of earlier incidents."
      />
      {isPending && <Skeleton className="h-24" />}
      {isError && (
        <p role="alert" className="text-xs text-destructive">
          Couldn't load your projects. Reload the page to try again.
        </p>
      )}
      {!isPending && !isError && !project && (
        <p className="text-sm text-muted-foreground">
          <Link to="/projects" className="text-primary hover:underline">
            Create a project
          </Link>{' '}
          first.
        </p>
      )}
      {project && <Uploader key={project.id} projectId={project.id} />}
    </div>
  )
}

function Uploader({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams()
  const kind: Kind = params.get('kind') === 'incidents' ? 'incidents' : 'logs'
  const [chosen, setChosen] = useState<{ file: File; id: number } | null>(null)
  const [mapping, setMapping] = useState<Mapping | null>(null) // null until a column is changed
  const [done, setDone] = useState<Result | null>(null)
  const queryClient = useQueryClient()

  const preview = useQuery({
    // `chosen` hashes as its id: the File itself has nothing JSON can see.
    queryKey: ['projects', projectId, 'upload-preview', kind, chosen, mapping],
    queryFn: () =>
      ok(
        api.POST('/projects/{project_id}/uploads/preview', {
          params: { path: { project_id: projectId } },
          ...uploadForm(kind, chosen!.file, mapping),
        }),
      ),
    enabled: chosen !== null && done === null,
    // While a new mapping is checked, the last check of the same file stays up.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[3] === kind && previousQuery.queryKey[4] === chosen ? previous : undefined,
    staleTime: Infinity, // a check is only redone when the file or the mapping changes
  })
  const upload = useMutation({
    mutationFn: (checked: Preview) =>
      ok(
        api.POST('/projects/{project_id}/uploads', {
          params: { path: { project_id: projectId } },
          ...uploadForm(kind, chosen!.file, checked.mapping),
        }),
      ),
    onSuccess: (result) => {
      setDone(result)
      void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'logs'] })
      void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'knowledge'] })
    },
  })

  const choose = (file: File | undefined) => {
    setChosen(file ? { file, id: (chosen?.id ?? 0) + 1 } : null)
    setMapping(null)
    upload.reset()
  }
  const startAgain = () => {
    setChosen(null)
    setMapping(null)
    setDone(null)
    upload.reset()
  }

  if (done) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Uploaded</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col items-start gap-4">
          <div role="status">
            <Outcome result={done} done />
          </div>
          <div className="flex gap-2">
            <Link to={KINDS[done.kind].open.to} className={buttonVariants()}>
              {KINDS[done.kind].open.label}
            </Link>
            <Button variant="outline" onClick={startAgain}>
              Upload another file
            </Button>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Choose a file</CardTitle>
          <CardDescription>{KINDS[kind].hint}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-6">
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-xs text-muted-foreground">It holds</legend>
            <div className="flex gap-4">
              {(Object.keys(KINDS) as Kind[]).map((value) => (
                <Label key={value} className="text-xs">
                  <input
                    type="radio"
                    name="kind"
                    value={value}
                    checked={kind === value}
                    onChange={() => {
                      setParams({ kind: value }, { replace: true })
                      setMapping(null) // our fields differ: guess again
                    }}
                  />
                  {KINDS[value].label}
                </Label>
              ))}
            </div>
          </fieldset>
          <Label className="flex flex-col items-start gap-1 text-xs text-muted-foreground">
            File (at most 20 MB)
            <Input
              key={chosen === null ? 'empty' : 'chosen'} // empties the box when starting again
              type="file"
              accept={ACCEPT}
              onChange={(event) => choose(event.target.files?.[0])}
              className="w-72"
            />
          </Label>
        </CardContent>
      </Card>
      {chosen && preview.isPending && <Skeleton className="h-60" />}
      {preview.isError && (
        <p role="alert" className="text-xs text-destructive">
          {errorMessage(preview.error)}
        </p>
      )}
      {preview.data && !preview.isError && (
        <PreviewCard
          preview={preview.data}
          checking={preview.isFetching}
          onMap={(field, column) => {
            const next = { ...preview.data.mapping, [field]: column }
            if (!column) delete next[field]
            setMapping(next)
          }}
          importing={upload.isPending}
          onImport={() => upload.mutate(preview.data)}
          error={upload.error}
        />
      )}
    </>
  )
}

function PreviewCard({
  preview,
  checking,
  onMap,
  importing,
  onImport,
  error,
}: {
  preview: Preview
  checking: boolean
  onMap: (field: string, column: string) => void
  importing: boolean
  onImport: () => void
  error: Error | null
}) {
  const { noun } = KINDS[preview.kind]
  const mapped = new Set(Object.values(preview.mapping))
  const unmapped = preview.columns.filter((column) => !mapped.has(column))
  const { result } = preview
  const rows = `${preview.rows.toLocaleString('en')} ${preview.rows === 1 ? 'row' : 'rows'}`

  return (
    <Card>
      <CardHeader>
        <CardTitle>{preview.filename}</CardTitle>
        <CardDescription>
          {rows}, read as {FORMATS[preview.format]}. Choose the column that fills each field; the
          ones with a * are needed.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="overflow-x-auto">
          <table aria-label="Fields and columns" className="w-full text-left text-xs">
            <thead className="border-b text-muted-foreground">
              <tr>
                <th className="py-2 pr-3 font-medium">Field</th>
                <th className="py-2 pr-3 font-medium">Column in the file</th>
                <th className="py-2 font-medium">In the first row</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {preview.fields.map((field) => (
                <tr key={field.name} className="align-top">
                  <td className="py-2 pr-3">
                    <code className="font-medium">{field.name}</code>
                    {field.required && <span aria-label="needed"> *</span>}
                    <span className="block text-muted-foreground">{field.description}</span>
                  </td>
                  <td className="py-2 pr-3">
                    <NativeSelect
                      aria-label={`Column for ${field.name}`}
                      value={preview.mapping[field.name] ?? ''}
                      onChange={(event) => onMap(field.name, event.target.value)}
                    >
                      <NativeSelectOption value="">Not in the file</NativeSelectOption>
                      {preview.columns.map((column) => (
                        <NativeSelectOption key={column} value={column}>
                          {column}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </td>
                  <td className="max-w-64 py-2 font-mono break-words text-muted-foreground">
                    {example(preview.sample[0], preview.mapping[field.name])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {unmapped.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {preview.kind === 'logs' ? 'Kept with each line as extra fields: ' : 'Not imported: '}
            {unmapped.join(', ')}
          </p>
        )}
        <div role="status" aria-busy={checking}>
          {preview.mapping_error && <p className="text-xs text-destructive">{preview.mapping_error}</p>}
          {result && <Outcome result={result} />}
        </div>
        <div className="flex flex-col items-start gap-1">
          <Button disabled={!result || result.imported === 0 || checking || importing} onClick={onImport}>
            {importing && 'Importing…'}
            {!importing && (result ? `Import ${result.imported.toLocaleString('en')} ${noun(result.imported)}` : 'Import')}
          </Button>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(error)}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** A column's value in a row, short enough for a table cell. */
function example(row: Record<string, unknown> | undefined, column: string | undefined): string {
  if (!row || !column || row[column] === undefined || row[column] === null) return ''
  const value = row[column]
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > 80 ? `${text.slice(0, 79)}…` : text
}

/** What an upload did, or (before it's done) would do. */
function Outcome({ result, done = false }: { result: Result; done?: boolean }) {
  const { noun } = KINDS[result.kind]
  const n = (count: number) => count.toLocaleString('en')
  const { imported, skipped, failed, errors } = result

  return (
    <div className="flex flex-col gap-1 text-xs">
      <p>
        {done
          ? `Imported ${n(imported)} ${noun(imported)} from ${result.filename}.`
          : `${n(imported)} ${noun(imported)} will be imported.`}
        {skipped > 0 &&
          ` ${n(skipped)} already in the project ${done ? (skipped === 1 ? 'was' : 'were') : 'will be'} skipped.`}
      </p>
      {failed > 0 && (
        <>
          <p className="text-destructive">
            {n(failed)} {failed === 1 ? 'row' : 'rows'} with a problem {done ? (failed === 1 ? 'was' : 'were') : 'will be'}{' '}
            left out{failed > errors.length ? `. The first ${errors.length}:` : ':'}
          </p>
          <ul aria-label="Problems" className="flex flex-col gap-0.5 font-mono text-muted-foreground">
            {errors.map((problem, index) => (
              <li key={index}>
                Row {problem.row}: {problem.message}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
