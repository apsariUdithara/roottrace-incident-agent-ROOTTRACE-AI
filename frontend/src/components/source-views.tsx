// One view per kind of source, for the source viewer's drawer. No syntax highlighting: what
// matters is the cited lines, which are marked.
import { ExternalLink } from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, type ReactNode } from 'react'
import { Link } from 'react-router'

import type { Schemas } from '@/api/types'
import { LevelBadge } from '@/components/log-level'
import { Badge } from '@/components/ui/badge'
import { SheetClose } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { selectProject } from '@/lib/current-project'
import { formatUtc } from '@/lib/time'

export type Source =
  | Schemas['FileSource']
  | Schemas['CommitSource']
  | Schemas['DocSource']
  | Schemas['LogSource']
  | Schemas['IncidentSource']
  | Schemas['IssueSource']
  | Schemas['MemorySource']

const DocMarkdown = lazy(() =>
  import('@/components/doc-markdown').then((module) => ({ default: module.DocMarkdown })),
)

export function SourceView({ source, projectId }: { source: Source; projectId: string }) {
  switch (source.kind) {
    case 'file':
      return <FileView source={source} />
    case 'commit':
      return <CommitView source={source} />
    case 'doc':
      return <DocView source={source} />
    case 'log':
      return <LogView source={source} projectId={projectId} />
    case 'incident':
      return <IncidentView incident={source.incident} />
    case 'issue':
      return <IssueView source={source} />
    case 'memory':
      return <MemoryView memory={source.memory} />
  }
}

function Meta({ children }: { children: ReactNode }) {
  return <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">{children}</p>
}

const short = (sha: string) => sha.slice(0, 7)

function FileView({ source }: { source: Schemas['FileSource'] }) {
  const lines = source.content.split('\n')
  if (lines.at(-1) === '') lines.pop() // the file's last newline
  const { start_line: start, end_line: end } = source
  const firstCited = useRef<HTMLTableRowElement>(null)

  useEffect(() => firstCited.current?.scrollIntoView?.({ block: 'center' }), [source])

  return (
    <>
      <Meta>
        <span className="font-mono text-foreground">{source.path}</span>
        <span>at {short(source.sha)}</span>
        {start !== null && end !== null && (
          <span>· cited: {start === end ? `line ${start}` : `lines ${start}–${end}`}</span>
        )}
      </Meta>
      <div className="overflow-x-auto rounded-md bg-muted/40 py-2">
        <table aria-label={source.path} className="w-full font-mono text-xs">
          <tbody>
            {lines.map((text, index) => {
              const number = index + 1
              const cited = start !== null && end !== null && number >= start && number <= end
              return (
                <tr
                  key={number}
                  ref={number === start ? firstCited : undefined}
                  data-cited={cited || undefined}
                  className={cited ? 'bg-primary/15' : undefined}
                >
                  <td className="w-12 pr-3 text-right align-top text-muted-foreground select-none">{number}</td>
                  <td className="pr-3 whitespace-pre">{text || ' '}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

function diffLineClass(line: string): string | undefined {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) return 'text-muted-foreground'
  if (line.startsWith('@@')) return 'text-muted-foreground'
  if (line.startsWith('+')) return 'bg-primary/10 text-primary'
  if (line.startsWith('-')) return 'bg-destructive/10 text-destructive'
  return undefined
}

function CommitView({ source }: { source: Schemas['CommitSource'] }) {
  const [subject, ...body] = source.message.split('\n')
  return (
    <>
      <div className="flex flex-col gap-1">
        <h3 className="font-heading text-sm font-semibold">{subject}</h3>
        {body.join('\n').trim() && (
          <p className="text-xs whitespace-pre-wrap text-muted-foreground">{body.join('\n').trim()}</p>
        )}
      </div>
      <Meta>
        <code>{short(source.sha)}</code>
        <span>· {source.author}</span>
        <span>· {formatUtc(source.committed_at)} UTC</span>
      </Meta>
      {source.files.length > 0 && (
        <ul aria-label="Changed files" className="flex flex-col font-mono text-xs">
          {source.files.map((file) => (
            <li key={file}>{file}</li>
          ))}
        </ul>
      )}
      <pre aria-label="Diff" className="overflow-x-auto rounded-md bg-muted/40 py-2 text-xs">
        {source.diff.split('\n').map((line, index) => (
          <div key={index} className={`px-3 ${diffLineClass(line) ?? ''}`}>
            {line || ' '}
          </div>
        ))}
      </pre>
    </>
  )
}

function DocView({ source }: { source: Schemas['DocSource'] }) {
  return (
    <>
      <Meta>
        {source.title && <span className="text-foreground">{source.title}</span>}
        <span className="font-mono">{source.path}</span>
      </Meta>
      <Suspense fallback={<Skeleton className="h-64" />}>
        <DocMarkdown markdown={source.markdown} section={source.section} />
      </Suspense>
    </>
  )
}

function LogView({ source, projectId }: { source: Schemas['LogSource']; projectId: string }) {
  const count = `${source.entries.length} ${source.entries.length === 1 ? 'line' : 'lines'}`
  return (
    <>
      <Meta>
        <span>{source.trace_id ? `${count} of trace ${source.trace_id}` : count}</span>
        {source.trace_id && (
          <SheetClose
            render={
              <Link
                to={`/logs?trace_id=${encodeURIComponent(source.trace_id)}`}
                onClick={() => selectProject(projectId)}
                className="text-primary hover:underline"
              />
            }
          >
            Open in Logs
          </SheetClose>
        )}
      </Meta>
      <div className="overflow-x-auto rounded-lg ring-1 ring-foreground/10">
        <table aria-label="Log lines" className="w-full text-left text-xs">
          <thead className="border-b text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Time (UTC)</th>
              <th className="px-3 py-2 font-medium">Level</th>
              <th className="px-3 py-2 font-medium">Instance</th>
              <th className="px-3 py-2 font-medium">Route</th>
              <th className="px-3 py-2 text-right font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Message</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {source.entries.map((line) => (
              <tr key={line.id} className="align-top">
                <td className="px-3 py-1.5 font-mono whitespace-nowrap text-muted-foreground">{formatUtc(line.ts)}</td>
                <td className="px-3 py-1.5">
                  <LevelBadge level={line.level} />
                </td>
                <td className="px-3 py-1.5 whitespace-nowrap">{line.instance}</td>
                <td className="px-3 py-1.5 font-mono whitespace-nowrap">{line.route}</td>
                <td className={`px-3 py-1.5 text-right ${line.status && line.status >= 500 ? 'text-destructive' : ''}`}>
                  {line.status || null}
                </td>
                <td className="min-w-64 px-3 py-1.5 break-words">{line.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  if (children === null || children === undefined || children === '') return null
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm whitespace-pre-wrap">{children}</dd>
    </div>
  )
}

function IncidentView({ incident }: { incident: Schemas['IncidentOut'] }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-heading text-sm font-semibold">
          {incident.key}: {incident.title}
        </h3>
        {incident.severity && <Badge variant="secondary">{incident.severity}</Badge>}
        <Badge variant="outline">{incident.status}</Badge>
      </div>
      <dl className="flex flex-col gap-3">
        <Field label="When (UTC)">{incident.occurred_at && formatUtc(incident.occurred_at)}</Field>
        <Field label="Symptoms">{incident.symptoms}</Field>
        <Field label="Root cause">{incident.root_cause}</Field>
        <Field label="Fix">{incident.fix}</Field>
        <Field label="Commit">{incident.commit_sha && <code>{short(incident.commit_sha)}</code>}</Field>
        <Field label="Tags">{incident.tags.length > 0 ? incident.tags.join(', ') : null}</Field>
      </dl>
    </>
  )
}

function IssueView({ source }: { source: Schemas['IssueSource'] }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-heading text-sm font-semibold">
          #{source.number} {source.title}
        </h3>
        {source.state && <Badge variant="outline">{source.state}</Badge>}
        {source.url && (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            Open on GitHub
            <ExternalLink className="size-3" aria-hidden />
          </a>
        )}
      </div>
      {source.body ? (
        <Suspense fallback={<Skeleton className="h-24" />}>
          <DocMarkdown markdown={source.body} />
        </Suspense>
      ) : (
        <p className="text-xs text-muted-foreground">No description.</p>
      )}
    </>
  )
}

const CATEGORIES: Record<Schemas['MemoryCategory'], string> = {
  user_pref: 'User preference',
  project_fact: 'Project fact',
  arch_decision: 'Architecture decision',
  previous_incident: 'Previous incident',
  investigation_context: 'Investigation context',
}

function MemoryView({ memory }: { memory: Schemas['MemoryOut'] }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{CATEGORIES[memory.category]}</Badge>
        {memory.status === 'archived' && <Badge variant="outline">archived</Badge>}
      </div>
      <p className="text-sm whitespace-pre-wrap">{memory.content}</p>
      <Meta>
        {/* Agent-written memories are labelled, so they aren't trusted blindly. */}
        <span>
          {memory.created_by === 'agent' ? 'Written by the agent: check it before relying on it' : 'Added by a person'}
        </span>
        <span>· updated {formatUtc(memory.updated_at)} UTC</span>
        {memory.source_ref && <span className="font-mono">· from {memory.source_ref}</span>}
      </Meta>
    </>
  )
}
