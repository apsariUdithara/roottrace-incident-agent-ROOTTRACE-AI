import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleDashed, Loader2, MinusCircle, Search, XCircle } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, ApiError, ok } from '@/api/client'
import type { Investigation } from '@/api/types'
import { Markdown } from '@/components/markdown'
import { PageHeader } from '@/components/page-header'
import { EvidenceChip } from '@/components/source-chip'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useCurrentProject } from '@/lib/current-project'
import { applyEvents, emptyView, stepLabel, type InvestigationView } from '@/lib/investigation'
import { useSourceViewer } from '@/lib/source-viewer'
import { formatUtc } from '@/lib/time'

const EXAMPLE = "Payment API returning 500s after today's deploy"
const POLL_MS = 1500

/* ----------------------------- list + start ----------------------------- */

export function InvestigationsPage() {
  const { project } = useCurrentProject()
  const navigate = useNavigate()
  const [question, setQuestion] = useState(EXAMPLE)

  const list = useQuery({
    queryKey: ['investigations', project?.id],
    queryFn: () =>
      ok(api.GET('/projects/{project_id}/investigations', { params: { path: { project_id: project!.id } } })),
    enabled: !!project,
  })

  const start = useMutation({
    mutationFn: () =>
      ok(
        api.POST('/projects/{project_id}/investigations', {
          params: { path: { project_id: project!.id } },
          body: { question: question.trim() },
        }),
      ),
    onSuccess: (investigation) => navigate(`/investigations/${investigation.id}`),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (question.trim().length >= 5 && !start.isPending) start.mutate()
  }

  if (!project) return <PageHeader title="Investigations" description="Create or pick a project first." />

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader
        title="Investigations"
        description={`Describe an incident in ${project.name}. The agent finds the root cause with cited evidence, then tests a fix in a Nebius Sandbox.`}
      />
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <Textarea
          aria-label="Incident"
          value={question}
          maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="flex items-center gap-3">
          <Button type="submit" size="sm" disabled={question.trim().length < 5 || start.isPending}>
            <Search data-icon="inline-start" />
            Investigate
          </Button>
          {start.isError && (
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(start.error)}
            </p>
          )}
        </div>
      </form>

      <ul aria-label="Past investigations" className="flex flex-col gap-2">
        {list.data?.map((item) => (
          <li key={item.id}>
            <Link to={`/investigations/${item.id}`} className="flex items-center gap-3 rounded-md border p-3 text-sm hover:bg-muted">
              <StatusBadge status={item.status} />
              <span className="flex-1 truncate">{item.question}</span>
              {item.confidence != null && <span className="text-xs">{pct(item.confidence)}</span>}
              <span className="text-xs text-muted-foreground">{formatUtc(item.started_at)}</span>
            </Link>
          </li>
        ))}
        {list.data?.length === 0 && <p className="text-sm text-muted-foreground">No investigations yet.</p>}
      </ul>
    </div>
  )
}

/* -------------------------------- detail -------------------------------- */

export function InvestigationPage() {
  const { investigationId = '' } = useParams()
  return <InvestigationDetail key={investigationId} investigationId={investigationId} />
}

// Keyed by id, so opening another investigation starts from an empty view.
function InvestigationDetail({ investigationId }: { investigationId: string }) {
  const view = useInvestigationEvents(investigationId)
  const info = useQuery({
    queryKey: ['investigation', investigationId, !!view.done],
    queryFn: () =>
      ok(api.GET('/investigations/{investigation_id}', { params: { path: { investigation_id: investigationId } } })),
  })
  const investigation = info.data

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={investigation?.question ?? 'Investigation'}
        description={<RunSummary view={view} investigation={investigation} />}
      />
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <Timeline view={view} />
        <Tabs defaultValue="report" className="min-w-0">
          <TabsList>
            <TabsTrigger value="report">Report</TabsTrigger>
            <TabsTrigger value="activity">Activity ({view.tools.length})</TabsTrigger>
            <TabsTrigger value="evidence">Evidence ({view.evidence.length})</TabsTrigger>
            <TabsTrigger value="patch" disabled={!view.patch}>Patch</TabsTrigger>
            <TabsTrigger value="tests" disabled={view.tests.length === 0}>Tests</TabsTrigger>
            <TabsTrigger value="pr" disabled={!view.pr}>PR draft</TabsTrigger>
            <TabsTrigger value="chat" disabled={!view.report}>Chat</TabsTrigger>
          </TabsList>
          <TabsContent value="report"><ReportTab view={view} projectId={investigation?.project_id} /></TabsContent>
          <TabsContent value="activity"><ActivityTab view={view} /></TabsContent>
          <TabsContent value="evidence"><EvidenceTab view={view} projectId={investigation?.project_id} /></TabsContent>
          <TabsContent value="patch"><PatchTab view={view} /></TabsContent>
          <TabsContent value="tests"><TestsTab view={view} /></TabsContent>
          <TabsContent value="pr"><PrTab view={view} investigationId={investigationId} /></TabsContent>
          <TabsContent value="chat"><ChatTab investigationId={investigationId} /></TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

/** The run's events, polled from `/events` until `done`. Polling rather than `/stream`: the
 * browser's EventSource can't send the Authorization header.
 * ponytail: 1.5 s poll; switch to /stream with fetch-event-source if the delay shows in the demo. */
function useInvestigationEvents(investigationId: string): InvestigationView {
  const [view, setView] = useState(emptyView)
  useEffect(() => {
    let stopped = false
    let current = emptyView()
    async function poll() {
      while (!stopped && !current.done) {
        try {
          const events = await ok(
            api.GET('/investigations/{investigation_id}/events', {
              params: { path: { investigation_id: investigationId }, query: { after: current.lastSeq } },
            }),
          )
          if (stopped) return
          if (events.length) {
            current = applyEvents(current, events)
            setView(current)
          }
        } catch {
          // a dropped request: try again on the next tick
        }
        if (!current.done) await new Promise((resolve) => setTimeout(resolve, POLL_MS))
      }
    }
    void poll()
    return () => {
      stopped = true
    }
  }, [investigationId])
  return view
}

function RunSummary({ view, investigation }: { view: InvestigationView; investigation?: Investigation }) {
  if (view.done) {
    const { status, duration_ms, total_tokens, total_cost_usd } = view.done
    return (
      <span>
        {status} · {(duration_ms / 1000).toFixed(0)} s · {total_tokens.toLocaleString()} tokens · ${total_cost_usd.toFixed(3)}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <Loader2 className="size-3 animate-spin" aria-hidden />
      {investigation?.status ?? 'loading'}…
    </span>
  )
}

function Timeline({ view }: { view: InvestigationView }) {
  return (
    <Card size="sm" className="self-start">
      <CardHeader>
        <CardTitle>Steps</CardTitle>
      </CardHeader>
      <CardContent>
        <ol aria-label="Steps" className="flex flex-col gap-2 text-sm">
          {view.steps.map((step, index) => (
            <li key={index} className="flex items-start gap-2">
              <StepIcon status={step.status} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className={step.status === 'skipped' ? 'text-muted-foreground' : ''}>{stepLabel(step.step)}</span>
                  {step.tier && <Badge variant="secondary" className="h-4 px-1 text-[10px]">{step.tier}</Badge>}
                  {step.durationMs != null && (
                    <span className="ml-auto text-xs text-muted-foreground">{(step.durationMs / 1000).toFixed(1)} s</span>
                  )}
                </div>
                {step.summary && <p className="truncate text-xs text-muted-foreground" title={step.summary}>{step.summary}</p>}
              </div>
            </li>
          ))}
          {view.steps.length === 0 && <li className="text-muted-foreground">Starting…</li>}
        </ol>
      </CardContent>
    </Card>
  )
}

function StepIcon({ status }: { status: string }) {
  if (status === 'running') return <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-primary" aria-label="running" />
  if (status === 'skipped') return <MinusCircle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="skipped" />
  return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" aria-label="done" />
}

function ReportTab({ view, projectId }: { view: InvestigationView; projectId?: string }) {
  const report = view.report
  if (!report) {
    return (
      <p className="text-sm text-muted-foreground">
        The report appears when the run finishes. {view.hypotheses.at(-1) && <>Current hypothesis: <em>{view.hypotheses.at(-1)!.statement}</em></>}
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Root cause</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm">{report.root_cause ?? 'No root cause was established.'}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            {report.verdict && <Badge variant="outline">verdict: {report.verdict}</Badge>}
            <Badge variant={report.fix === 'verified' ? 'default' : 'outline'}>fix: {report.fix}</Badge>
          </div>
        </CardContent>
      </Card>
      {view.confidence && <ConfidenceCard view={view} />}
      <Markdown>{report.summary}</Markdown>
      {report.next_steps.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Next steps</h3>
          <ul className="list-disc pl-5 text-sm">
            {report.next_steps.map((step) => <li key={step}>{step}</li>)}
          </ul>
        </div>
      )}
      <EvidenceTab view={view} projectId={projectId} chipsOnly />
    </div>
  )
}

/** The score and the parts it's computed from (agent/confidence.py): the score is code, not a guess. */
function ConfidenceCard({ view }: { view: InvestigationView }) {
  const { score, components } = view.confidence!
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Confidence {pct(score)}</CardTitle>
      </CardHeader>
      <CardContent>
        <table aria-label="Confidence breakdown" className="w-full text-xs">
          <tbody>
            {components.map((c) => (
              <tr key={c.name}>
                <td className="py-0.5 capitalize">{c.name}</td>
                <td className="w-1/2 py-0.5">
                  <div className="h-1.5 rounded bg-muted">
                    <div className={`h-1.5 rounded ${c.weight < 0 ? 'bg-destructive' : 'bg-primary'}`} style={{ width: pct(c.value) }} />
                  </div>
                </td>
                <td className="py-0.5 text-right text-muted-foreground">
                  {c.weight > 0 ? '+' : '−'}
                  {Math.abs(c.weight).toFixed(2)} × {c.value.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

function ActivityTab({ view }: { view: InvestigationView }) {
  return (
    <ol aria-label="Tool calls" className="flex flex-col gap-2">
      {view.tools.map((call) => (
        <li key={call.call_id}>
          <details className="rounded-md border p-2 text-sm">
            <summary className="flex cursor-pointer items-center gap-2">
              {!call.result ? (
                <Loader2 className="size-4 animate-spin" aria-label="running" />
              ) : call.result.ok ? (
                <CheckCircle2 className="size-4 text-primary" aria-label="ok" />
              ) : (
                <XCircle className="size-4 text-destructive" aria-label="failed" />
              )}
              <code className="font-semibold">{call.tool}</code>
              {call.transport === 'mcp' && <Badge variant="secondary">MCP</Badge>}
              <span className="truncate text-muted-foreground">{call.result?.summary ?? 'running…'}</span>
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(call.arguments, null, 2)}</pre>
            {call.result && (
              <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
                {call.result.error ?? call.result.output_preview}
              </pre>
            )}
          </details>
        </li>
      ))}
      {view.tools.length === 0 && <p className="text-sm text-muted-foreground">No tool calls yet.</p>}
    </ol>
  )
}

function EvidenceTab({ view, projectId, chipsOnly = false }: { view: InvestigationView; projectId?: string; chipsOnly?: boolean }) {
  const openSource = useSourceViewer()
  const open = (ref?: string | null) => (projectId && ref ? () => openSource(projectId, ref) : undefined)
  if (chipsOnly) {
    return (
      <ul aria-label="Evidence" className="flex flex-wrap gap-1.5">
        {view.evidence.map((e) => (
          <li key={e.evidence_id} className="max-w-full"><EvidenceChip evidence={e} onOpen={open(e.source_ref)} /></li>
        ))}
      </ul>
    )
  }
  return (
    <ul aria-label="Evidence" className="flex flex-col gap-2">
      {view.evidence.map((e) => (
        <li key={e.evidence_id} className="flex flex-col gap-1 rounded-md border p-2 text-sm">
          <div className="flex items-center gap-2">
            <EvidenceChip evidence={e} onOpen={open(e.source_ref)} />
            {e.stance !== 'neutral' && (
              <Badge variant={e.stance === 'contradicts' ? 'destructive' : 'outline'}>{e.stance}</Badge>
            )}
          </div>
          <p>{e.statement}</p>
          {e.excerpt && <pre className="overflow-x-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">{e.excerpt}</pre>}
        </li>
      ))}
      {view.evidence.length === 0 && <p className="text-sm text-muted-foreground">No evidence yet.</p>}
    </ul>
  )
}

function PatchTab({ view }: { view: InvestigationView }) {
  const patch = view.patch
  if (!patch) return null
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <span>Attempt {patch.attempt}</span>
        {view.patchStatus && <Badge variant="outline">{view.patchStatus.status}</Badge>}
      </div>
      {patch.explanation && <Markdown>{patch.explanation}</Markdown>}
      <ul className="list-disc pl-5 text-sm">
        {patch.files.map((f) => (
          <li key={f.path}><code>{f.path}</code>{f.explanation && <> — {f.explanation}</>}</li>
        ))}
      </ul>
      <Diff diff={patch.diff} />
    </div>
  )
}

/** A unified diff, coloured by line. */
function Diff({ diff }: { diff: string }) {
  return (
    <pre aria-label="Diff" className="overflow-x-auto rounded-md border bg-muted/40 p-2 text-xs leading-5">
      {diff.split('\n').map((line, index) => {
        const tone = line.startsWith('+') && !line.startsWith('+++')
          ? 'bg-primary/15'
          : line.startsWith('-') && !line.startsWith('---')
            ? 'bg-destructive/15'
            : line.startsWith('@@')
              ? 'text-muted-foreground'
              : ''
        return <div key={index} className={tone}>{line || ' '}</div>
      })}
    </pre>
  )
}

/** Baseline (should fail: the bug is reproduced) and patched (should pass), in a Nebius Sandbox. */
function TestsTab({ view }: { view: InvestigationView }) {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      {view.tests.map((run) => {
        const r = run.result
        const failed = r ? r.exit_code !== 0 : undefined
        return (
          <Card key={run.phase} size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {failed === undefined ? (
                  <CircleDashed className="size-4 animate-spin" aria-hidden />
                ) : failed ? (
                  <XCircle className="size-4 text-destructive" aria-hidden />
                ) : (
                  <CheckCircle2 className="size-4 text-primary" aria-hidden />
                )}
                <span className="capitalize">{run.phase}</span>
                {r && (
                  <span className="text-xs font-normal text-muted-foreground">
                    {r.passed ?? 0} passed · {r.failed ?? 0} failed · {(r.duration_ms / 1000).toFixed(1)} s
                    {r.as_expected && ' · as expected'}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {r && <code className="text-xs text-muted-foreground">$ {r.command}</code>}
              <pre className="max-h-80 overflow-auto rounded bg-black p-2 text-xs text-green-200">{run.lines.join('\n')}</pre>
              {r?.sandbox_image_id && <p className="text-xs text-muted-foreground">Nebius Sandbox image {r.sandbox_image_id}</p>}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

function PrTab({ view, investigationId }: { view: InvestigationView; investigationId: string }) {
  const approve = useMutation({
    mutationFn: () =>
      ok(api.POST('/investigations/{investigation_id}/approve', { params: { path: { investigation_id: investigationId } } })),
  })
  const pr = view.pr
  if (!pr) return null
  const url = approve.data?.url ?? view.prUrl
  return (
    <div className="flex flex-col gap-3">
      <h3 className="font-semibold">{pr.title}</h3>
      <Markdown>{pr.body}</Markdown>
      <div className="flex items-center gap-3">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" className="text-sm text-primary underline">Draft PR opened: {url}</a>
        ) : (
          <Button size="sm" onClick={() => approve.mutate()} disabled={approve.isPending || view.report?.fix !== 'verified'}>
            Approve and open draft PR
          </Button>
        )}
        {approve.isError && <p role="alert" className="text-xs text-destructive">{errorMessage(approve.error)}</p>}
      </div>
    </div>
  )
}

type ChatTurn = { role: string; content: string }

/** Follow-up questions about this investigation. The list endpoint isn't built yet (B-701), so
 * the conversation lives in the page. */
function ChatTab({ investigationId }: { investigationId: string }) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const ask = useMutation({
    mutationFn: (content: string) =>
      ok(api.POST('/investigations/{investigation_id}/messages', {
        params: { path: { investigation_id: investigationId } },
        body: { content },
      })),
    onSuccess: (message) => setTurns((current) => [...current, message]),
  })
  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (content.length < 2 || ask.isPending) return
    setTurns((current) => [...current, { role: 'user', content }])
    setDraft('')
    ask.mutate(content)
  }
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      {turns.map((turn, index) =>
        turn.role === 'user' ? (
          <div key={index} className="self-end rounded-lg bg-muted px-3 py-2 text-sm">{turn.content}</div>
        ) : (
          <Markdown key={index}>{turn.content}</Markdown>
        ),
      )}
      {ask.isPending && <p className="text-sm text-muted-foreground">Thinking…</p>}
      {ask.isError && <p role="alert" className="text-xs text-destructive">{errorMessage(ask.error)}</p>}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <Textarea aria-label="Follow-up question" placeholder="Which commit introduced this?" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" size="sm" disabled={draft.trim().length < 2 || ask.isPending}>Ask</Button>
      </form>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const variant = status === 'failed' ? 'destructive' : status === 'completed' ? 'default' : 'outline'
  return <Badge variant={variant}>{status}</Badge>
}

const pct = (value: number) => `${Math.round(value * 100)}%`

function errorMessage(error: Error): string {
  if (error instanceof ApiError) return error.message
  return "Can't reach the server. Check your connection and try again."
}
