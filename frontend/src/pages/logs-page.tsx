// /logs: the current project's structured logs, the lines the agent's search_logs tool reads.
// The filters and page live in the URL (?level=ERROR&route=/payments&offset=100), so a view can
// be shared, and the filters match as in the agent's tool. Times are UTC, as in the agent's
// evidence.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { LevelBadge } from '@/components/log-level'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { statusTone } from '@/lib/colors'
import { useCurrentProject } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'
import { useDebouncedParam, useSearchParamsUpdate, type Changes } from '@/lib/search-params'
import { formatUtc } from '@/lib/time'

type Facets = Schemas['LogFacets']
type LogLine = Schemas['LogEntryOut']

const PAGE_SIZE = 50 // short pages: a long table is hard to scan
const FILTERS = ['level', 'service', 'route', 'error_type', 'text', 'trace_id', 'since', 'until']
const NO_FILTERS: Changes = { ...Object.fromEntries(FILTERS.map((name) => [name, null])), offset: null }

export function LogsPage() {
  const { project, isPending, isError } = useCurrentProject()

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Logs"
        description={
          <>
            This project's structured logs, newest first: the lines the agent's log search reads.
            Times are UTC.{' '}
            <Link to="/upload?kind=logs" className="text-primary hover:underline">
              Upload logs
            </Link>
          </>
        }
      />
      {isPending && <Skeleton className="h-24" />}
      {isError && (
        <p role="alert" className="text-[0.8125rem] text-destructive">
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
      {project && <ProjectLogs key={project.id} projectId={project.id} />}
    </div>
  )
}

function ProjectLogs({ projectId }: { projectId: string }) {
  const facets = useQuery({
    queryKey: ['projects', projectId, 'logs', 'facets'],
    queryFn: () =>
      ok(
        api.GET('/projects/{project_id}/logs/facets', {
          params: { path: { project_id: projectId } },
        }),
      ),
  })

  if (facets.isPending) return <Skeleton className="h-40" />
  if (facets.isError) {
    return (
      <p role="alert" className="text-[0.8125rem] text-destructive">
        {errorMessage(facets.error)}
      </p>
    )
  }
  if (facets.data.total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No logs yet</CardTitle>
          <CardDescription>
            This project has no log lines for the agent to search.{' '}
            <Link to="/upload?kind=logs" className="text-primary hover:underline">
              Upload a log file
            </Link>
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }
  return <LogExplorer projectId={projectId} facets={facets.data} />
}

function LogExplorer({ projectId, facets }: { projectId: string; facets: Facets }) {
  const [params] = useSearchParams()
  const update = useSearchParamsUpdate()
  const filters = Object.fromEntries(FILTERS.map((name) => [name, params.get(name) || undefined]))
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const filtered = FILTERS.some((name) => params.get(name))
  // Said here rather than asked: the backend would refuse it.
  const backwards = Boolean(filters.since && filters.until && toInput(filters.since) > toInput(filters.until))

  const lines = useQuery({
    queryKey: ['projects', projectId, 'logs', { ...filters, offset }],
    queryFn: () =>
      ok(
        api.GET('/projects/{project_id}/logs', {
          params: {
            path: { project_id: projectId },
            query: { ...filters, limit: PAGE_SIZE, offset },
          },
        }),
      ),
    enabled: !backwards,
    placeholderData: keepPreviousData, // the old page stays up while the next one loads
  })
  const clear = () => update(NO_FILTERS)

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        {facets.total.toLocaleString('en')} lines, from {formatUtc(facets.first_ts!)} to{' '}
        {formatUtc(facets.last_ts!)}
      </p>
      <LogFilters facets={facets} update={update} onClear={filtered ? clear : undefined} />
      {backwards ? (
        <p role="alert" className="text-[0.8125rem] text-destructive">
          “From” is after “To”, so no line can match.
        </p>
      ) : (
        lines.isPending && <Skeleton className="h-60" />
      )}
      {lines.isError && (
        <p role="alert" className="text-[0.8125rem] text-destructive">
          {errorMessage(lines.error)}
        </p>
      )}
      {lines.data && !backwards && (
        <LogTable
          page={lines.data}
          showService={facets.services.length > 1}
          onTrace={(traceId) => update({ ...NO_FILTERS, trace_id: traceId })} // all its lines
          onPage={(next) => update({ offset: next })}
          onClear={clear}
        />
      )}
    </div>
  )
}

function LogFilters({
  facets,
  update,
  onClear,
}: {
  facets: Facets
  update: (changes: Changes) => void
  onClear?: () => void
}) {
  const [params] = useSearchParams()
  const [route, setRoute] = useDebouncedParam('route')
  const [text, setText] = useDebouncedParam('text')
  const routes = useId()
  const traceId = params.get('trace_id')
  const service = params.get('service') ?? ''
  const pick = (name: string) => (value: string) => update({ [name]: value, offset: null })

  const level = (params.get('level') ?? '').toUpperCase()
  return (
    <div className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[0.8125rem] text-muted-foreground">Quick filters:</span>
        <Button
          size="sm"
          variant="outline"
          className={level === 'ERROR' ? statusTone('failed') : ''}
          onClick={() => update({ level: level === 'ERROR' ? null : 'ERROR', offset: null })}
        >
          Errors only
        </Button>
        <Button
          size="sm"
          variant="outline"
          className={level === 'WARN' ? statusTone('weak') : ''}
          onClick={() => update({ level: level === 'WARN' ? null : 'WARN', offset: null })}
        >
          Warnings only
        </Button>
      </div>
    <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <FacetSelect
        label="Level"
        any="Any level"
        values={facets.levels}
        // The filter ignores case, and the facets list levels in capitals.
        value={(params.get('level') ?? '').toUpperCase()}
        onChange={pick('level')}
      />
      {(facets.services.length > 1 || service) && (
        <FacetSelect
          label="Service"
          any="Any service"
          values={facets.services}
          value={service}
          onChange={pick('service')}
        />
      )}
      <FacetSelect
        label="Error type"
        any="Any error type"
        values={facets.error_types}
        value={params.get('error_type') ?? ''}
        onChange={pick('error_type')}
      />
      <FilterField label="Route">
        <Input
          list={routes}
          placeholder="Route, or part of it"
          value={route}
          maxLength={500}
          onChange={(event) => setRoute(event.target.value)}
          className="w-full"
        />
        <datalist id={routes}>
          {facets.routes.map(({ value }) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      </FilterField>
      <FilterField label="Message">
        <Input
          type="search"
          placeholder="Text in the message"
          value={text}
          maxLength={200}
          onChange={(event) => setText(event.target.value)}
          className="w-full"
        />
      </FilterField>
      <TimeField label="From (UTC)" value={params.get('since')} onChange={pick('since')} />
      <TimeField label="To (UTC)" value={params.get('until')} onChange={pick('until')} />
      {traceId && (
        <Badge variant="outline" className="h-7 gap-1 font-mono">
          trace {traceId}
          <button
            type="button"
            aria-label="Stop showing only this trace"
            onClick={() => update({ trace_id: null, offset: null })}
            className="rounded-full hover:text-destructive"
          >
            <X aria-hidden />
          </button>
        </Badge>
      )}
      {onClear && (
        <Button variant="ghost" size="sm" onClick={onClear}>
          Clear filters
        </Button>
      )}
    </div>
    </div>
  )
}

function FilterField({ label, children }: { label: string; children: ReactNode }) {
  // The label wraps its field, so the field is named by it.
  return (
    <Label className="flex flex-col items-stretch gap-1 text-[0.8125rem] text-muted-foreground">
      {label}
      {children}
    </Label>
  )
}

function FacetSelect({
  label,
  any,
  values,
  value,
  onChange,
}: {
  label: string
  any: string
  values: Schemas['LogValue'][]
  value: string
  onChange: (value: string) => void
}) {
  // A value from a shared link that the logs don't have still shows as chosen.
  const options = value && !values.some((item) => item.value === value) ? [{ value, count: 0 }, ...values] : values

  return (
    <FilterField label={label}>
      <NativeSelect value={value} onChange={(event) => onChange(event.target.value)}>
        <NativeSelectOption value="">{any}</NativeSelectOption>
        {options.map((item) => (
          <NativeSelectOption key={item.value} value={item.value}>
            {item.value} ({item.count.toLocaleString('en')})
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </FilterField>
  )
}

function TimeField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string | null
  onChange: (value: string) => void
}) {
  return (
    <FilterField label={label}>
      <Input
        type="datetime-local"
        step={1}
        value={toInput(value)}
        onChange={(event) => onChange(fromInput(event.target.value))}
        className="w-full"
      />
    </FilterField>
  )
}

const ZONE = /(Z|[+-]\d\d:?\d\d)$/i

/** A time from the URL as a datetime-local value in UTC: "2026-10-06T14:26:26Z" → "2026-10-06T14:26:26".
 * A time without a timezone is UTC, as the backend reads it. */
function toInput(value: string | null): string {
  if (!value) return ''
  const time = new Date(ZONE.test(value) ? value : `${value}Z`)
  return Number.isNaN(time.getTime()) ? '' : time.toISOString().slice(0, 19)
}

/** A datetime-local value, read as UTC, for the URL. Browsers leave out ":00" seconds. */
function fromInput(value: string): string {
  if (!value) return ''
  return `${value.length === 16 ? `${value}:00` : value}Z`
}

function LogTable({
  page,
  showService,
  onTrace,
  onPage,
  onClear,
}: {
  page: Schemas['LogPage']
  showService: boolean
  onTrace: (traceId: string) => void
  onPage: (offset: number) => void
  onClear: () => void
}) {
  if (page.total === 0) {
    return (
      <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
        No lines match these filters.
        <Button variant="link" size="sm" onClick={onClear}>
          Clear filters
        </Button>
      </div>
    )
  }
  const first = page.offset + 1
  const last = page.offset + page.items.length

  return (
    <>
      <div className="overflow-x-auto rounded-lg ring-1 ring-foreground/10">
        <table aria-label="Log lines" className="w-full text-left text-[0.8125rem]">
          <thead className="border-b text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Time (UTC)</th>
              <th className="px-3 py-2 font-medium">Level</th>
              {showService && <th className="px-3 py-2 font-medium">Service</th>}
              <th className="px-3 py-2 font-medium">Instance</th>
              <th className="px-3 py-2 font-medium">Route</th>
              <th className="px-3 py-2 text-right font-medium">Status</th>
              <th className="px-3 py-2 text-right font-medium">Latency</th>
              <th className="px-3 py-2 font-medium">Message</th>
              <th className="px-3 py-2 font-medium">Trace</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {page.items.map((line) => (
              <LogRow key={line.id} line={line} showService={showService} onTrace={onTrace} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
        <span>
          {first.toLocaleString('en')}–{last.toLocaleString('en')} of {page.total.toLocaleString('en')}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={page.offset === 0}
          onClick={() => onPage(Math.max(0, page.offset - page.limit))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={last >= page.total}
          onClick={() => onPage(page.offset + page.limit)}
        >
          Next
        </Button>
      </div>
    </>
  )
}

function LogRow({
  line,
  showService,
  onTrace,
}: {
  line: LogLine
  showService: boolean
  onTrace: (traceId: string) => void
}) {
  // Most error lines start with their error type; the others get it as a label.
  const label = line.error_type && !line.message.startsWith(line.error_type) ? line.error_type : null
  const attrs = Object.entries(line.attrs)

  return (
    <tr className="align-top">
      <td className="px-3 py-1.5 font-mono whitespace-nowrap text-muted-foreground">{formatUtc(line.ts)}</td>
      <td className="px-3 py-1.5">
        <LevelBadge level={line.level} />
      </td>
      {showService && <td className="px-3 py-1.5 whitespace-nowrap">{line.service}</td>}
      <td className="px-3 py-1.5 whitespace-nowrap">{line.instance}</td>
      <td className="px-3 py-1.5 font-mono whitespace-nowrap">{line.route}</td>
      <td className={`px-3 py-1.5 text-right ${line.status && line.status >= 500 ? 'text-destructive' : ''}`}>
        {line.status || null}
      </td>
      <td className="px-3 py-1.5 text-right whitespace-nowrap">
        {line.latency_ms ? `${line.latency_ms.toLocaleString('en')} ms` : null}
      </td>
      <td className="min-w-64 px-3 py-1.5 break-words">
        {label && <span className="mr-1.5 font-mono text-muted-foreground">{label}</span>}
        {line.message}
        {attrs.length > 0 && (
          <span className="block font-mono text-muted-foreground">
            {attrs.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(' ')}
          </span>
        )}
      </td>
      <td className="px-3 py-1.5">
        {line.trace_id && (
          <button
            type="button"
            title={`Every line of trace ${line.trace_id}`}
            onClick={() => onTrace(line.trace_id!)}
            className="font-mono text-primary hover:underline"
          >
            {line.trace_id.slice(0, 8)}
          </button>
        )}
      </td>
    </tr>
  )
}
