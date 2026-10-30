import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronRight, CircleDashed, Loader2, MinusCircle, Search, XCircle } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, ApiError, ok } from '@/api/client'
import type { Investigation } from '@/api/types'
import { Markdown } from '@/components/markdown'
import { useShowMore } from '@/components/show-more'
import { PageHeader } from '@/components/page-header'
import { EvidenceChip, SOURCE_ICONS } from '@/components/source-chip'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { SOURCE_TONE, statusTone } from '@/lib/colors'
import { useCurrentProject } from '@/lib/current-project'
import { applyEvents, emptyView, stepLabel, type InvestigationView } from '@/lib/investigation'
import type { SourceKind } from '@/lib/source-refs'
import { useSourceViewer } from '@/lib/source-viewer'
import { formatShort } from '@/lib/time'

// The demo's incident happened on 6 Oct 2026 (its logs end that day): asking about "today" would
// send the agent to a day with no errors, and it would rightly refuse to confirm a cause.
const EXAMPLE = 'The payment API started returning 500 errors after the v1.4.8 deployment on 6 October 2026. Find out why.'
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
            <p role="alert" className="text-[0.8125rem] text-destructive">
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
              {item.confidence != null && <span className="text-[0.8125rem]">{pct(item.confidence)}</span>}
              <span className="text-[0.8125rem] text-muted-foreground">{formatShort(item.started_at)}</span>
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
      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
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
            <TabsTrigger value="trace">Trace</TabsTrigger>
          </TabsList>
          <TabsContent value="report"><ReportTab view={view} projectId={investigation?.project_id} /></TabsContent>
          <TabsContent value="activity"><ActivityTab view={view} /></TabsContent>
          <TabsContent value="evidence"><EvidenceTab view={view} projectId={investigation?.project_id} /></TabsContent>
          <TabsContent value="patch"><PatchTab view={view} /></TabsContent>
          <TabsContent value="tests"><TestsTab view={view} /></TabsContent>
          <TabsContent value="pr"><PrTab view={view} investigationId={investigationId} /></TabsContent>
          <TabsContent value="chat"><ChatTab investigationId={investigationId} /></TabsContent>
          <TabsContent value="trace"><TraceTab investigationId={investigationId} done={!!view.done} /></TabsContent>
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

/** The agent's 16 steps, grouped into the five phases a person thinks in. A finished phase
 * folds to one line (its status and time); the phase that is running stays open. */
const PHASES: { name: string; steps: string[] }[] = [
  { name: 'Understand', steps: ['triage', 'recall_memory', 'plan'] },
  { name: 'Investigate', steps: ['investigate', 'compare_evidence'] },
  { name: 'Diagnose', steps: ['hypothesize', 'verify', 'score_confidence'] },
  { name: 'Fix and test', steps: ['generate_patch', 'run_tests', 'verify_fix'] },
  { name: 'Report', steps: ['write_report', 'draft_pr', 'save_memories', 'await_approval', 'create_pr'] },
]

function Timeline({ view }: { view: InvestigationView }) {
  return (
    <Card size="sm" className="self-start">
      <CardHeader>
        <CardTitle>Progress</CardTitle>
      </CardHeader>
      <CardContent>
        <ol aria-label="Steps" className="flex flex-col gap-1.5 text-sm">
          {PHASES.map((phase) => {
            const steps = view.steps.filter((s) => phase.steps.includes(s.step))
            const status = steps.length === 0 ? 'pending' : steps.some((s) => s.status === 'running') ? 'running' : 'done'
            const ms = steps.reduce((sum, s) => sum + (s.durationMs ?? 0), 0)
            return (
              <li key={phase.name}>
                <details open={status === 'running'} className="group rounded-lg">
                  <summary
                    className={`flex cursor-pointer list-none items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-muted ${status === 'running' ? 'bg-primary/8 ring-1 ring-primary/20' : ''}`}
                  >
                    <StepIcon status={status} />
                    <span className={`font-medium ${status === 'pending' ? 'text-muted-foreground' : ''}`}>{phase.name}</span>
                    {steps.length > 0 && (
                      <span className="text-[0.8125rem] text-muted-foreground">
                        {steps.length} step{steps.length === 1 ? '' : 's'}
                      </span>
                    )}
                    {ms > 0 && (
                      <span className="ml-auto text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums">
                        {(ms / 1000).toFixed(0)} s
                      </span>
                    )}
                    {steps.length > 0 && (
                      <ChevronRight className={`size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90 ${ms > 0 ? '' : 'ml-auto'}`} />
                    )}
                  </summary>
                  <ol className="mt-1 ml-[1.15rem] flex flex-col gap-1 border-l pl-3">
                    {steps.map((step, index) => (
                      <li key={index} className="flex items-start gap-2 py-1">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <span className={step.status === 'skipped' ? 'text-muted-foreground' : ''}>{stepLabel(step.step)}</span>
                            {step.tier && <TierBadge tier={step.tier} />}
                            {step.status === 'running' && <Loader2 className="size-3.5 animate-spin text-sky-500" aria-label="running" />}
                            {step.durationMs != null && (
                              <span className="ml-auto text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums">
                                {(step.durationMs / 1000).toFixed(1)} s
                              </span>
                            )}
                          </div>
                          {step.summary && (
                            <p className="truncate text-[0.8125rem] text-muted-foreground" title={step.summary}>{step.summary}</p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                </details>
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}

/** The model size, coloured so the routing (Nano → Super → Ultra) shows at a glance. */
const TIER_STYLE: Record<string, string> = {
  nano: 'bg-sky-500/12 text-sky-700 dark:text-sky-300',
  super: 'bg-violet-500/12 text-violet-700 dark:text-violet-300',
  ultra: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
}

function TierBadge({ tier }: { tier: string }) {
  return <Badge className={`h-5 px-1.5 text-xs capitalize ${TIER_STYLE[tier] ?? ''}`}>{tier}</Badge>
}

function StepIcon({ status }: { status: string }) {
  if (status === 'running') return <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-sky-500" aria-label="running" />
  if (status === 'pending') return <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="pending" />
  if (status === 'skipped') return <MinusCircle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="skipped" />
  return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="done" />
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
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        {/* The answer first, in large type: what judges and on-call engineers look for. */}
        <Card className="border-l-4 border-l-primary">
          <CardHeader>
            <CardDescription className="font-medium tracking-wide uppercase">Root cause</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <p className="text-lg leading-relaxed font-medium">{report.root_cause ?? 'No root cause was established.'}</p>
            <div className="flex flex-wrap gap-2">
              {report.verdict && (
                <Badge variant="outline" className={statusTone(report.verdict)}>verdict: {report.verdict}</Badge>
              )}
              <Badge variant="outline" className={statusTone(report.fix)}>
                {report.fix === 'verified' ? 'fix verified in Nebius Sandbox' : `fix: ${report.fix}`}
              </Badge>
            </div>
          </CardContent>
        </Card>
        {view.confidence && <ConfidenceCard view={view} />}
      </div>
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

const COMPONENT_BAR: Record<string, string> = {
  coverage: 'bg-sky-500',
  strength: 'bg-violet-500',
  verifier: 'bg-emerald-500',
  history: 'bg-amber-500',
  fix: 'bg-teal-500',
  contradiction: 'bg-red-500',
}

/** The score and the parts it's computed from (agent/confidence.py): the score is code, not a guess. */
function ConfidenceCard({ view }: { view: InvestigationView }) {
  const { score, components } = view.confidence!
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription className="font-medium tracking-wide uppercase">Confidence</CardDescription>
        <CardTitle className="font-sans text-4xl font-bold text-primary tabular-nums">{pct(score)}</CardTitle>
        <p className="text-[0.8125rem] text-muted-foreground">computed from the evidence, not guessed</p>
      </CardHeader>
      <CardContent>
        <table aria-label="Confidence breakdown" className="w-full text-[0.8125rem]">
          <tbody>
            {components.map((c) => (
              <tr key={c.name}>
                <td className="py-0.5 capitalize">{c.name}</td>
                <td className="w-1/2 py-0.5">
                  <div className="h-1.5 rounded bg-muted">
                    <div className={`h-1.5 rounded ${COMPONENT_BAR[c.name] ?? 'bg-primary'}`} style={{ width: pct(c.value) }} />
                  </div>
                </td>
                <td className="py-0.5 pl-2 text-right whitespace-nowrap text-muted-foreground tabular-nums">
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

/** Each tool in plain words, with the source kind it reads (for its icon and colour). */
const TOOLS: Record<string, { label: string; kind: SourceKind }> = {
  search_logs: { label: 'Searched the logs', kind: 'log' },
  search_github_commits: { label: 'Searched the commits', kind: 'commit' },
  get_commit_diff: { label: 'Read a commit', kind: 'commit' },
  search_code: { label: 'Searched the code', kind: 'code' },
  get_file: { label: 'Read a file', kind: 'code' },
  search_documents: { label: 'Searched the docs', kind: 'doc' },
  search_github_issues: { label: 'Searched the issues', kind: 'issue' },
  search_previous_incidents: { label: 'Searched past incidents', kind: 'incident' },
  get_incident: { label: 'Read a past incident', kind: 'incident' },
  query_database: { label: 'Queried the database', kind: 'database' },
}

function ActivityTab({ view }: { view: InvestigationView }) {
  return (
    <ol aria-label="Tool calls" className="flex flex-col gap-2">
      {view.tools.map((call) => {
        const tool = TOOLS[call.tool]
        const Icon = tool ? SOURCE_ICONS[tool.kind] : Search
        return (
        <li key={call.call_id}>
          <details className="group rounded-lg border bg-card text-sm">
            <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2.5">
              <span className={`flex size-8 shrink-0 items-center justify-center rounded-md border ${tool ? SOURCE_TONE[tool.kind] : ''}`}>
                <Icon className="size-4" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{tool?.label ?? call.tool}</span>
                  {call.transport === 'mcp' && <Badge variant="secondary">MCP</Badge>}
                  <code className="hidden text-xs text-muted-foreground sm:inline">{call.tool}</code>
                </div>
                <p className="truncate text-[0.8125rem] text-muted-foreground">{call.result?.summary ?? 'running…'}</p>
              </div>
              {!call.result ? (
                <Loader2 className="size-4 shrink-0 animate-spin text-sky-500" aria-label="running" />
              ) : call.result.ok ? (
                <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="ok" />
              ) : (
                <XCircle className="size-4 shrink-0 text-red-600 dark:text-red-400" aria-label="failed" />
              )}
              <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden />
            </summary>
            <div className="border-t px-3 pb-3">
            <p className="mt-2 text-xs font-medium text-muted-foreground uppercase">Asked</p>
            <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-[0.8125rem]">{JSON.stringify(call.arguments, null, 2)}</pre>
            {call.result && <p className="mt-2 text-xs font-medium text-muted-foreground uppercase">Found</p>}
            {call.result && (
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 text-[0.8125rem] whitespace-pre-wrap">
                {call.result.error ?? call.result.output_preview}
              </pre>
            )}
            </div>
          </details>
        </li>
        )
      })}
      {view.tools.length === 0 && <p className="text-sm text-muted-foreground">No tool calls yet.</p>}
    </ol>
  )
}

function EvidenceTab({ view, projectId, chipsOnly = false }: { view: InvestigationView; projectId?: string; chipsOnly?: boolean }) {
  const openSource = useSourceViewer()
  const open = (ref?: string | null) => (projectId && ref ? () => openSource(projectId, ref) : undefined)
  const chips = useShowMore(view.evidence, 8)
  if (chipsOnly) {
    return (
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Evidence cited</h3>
        <ul aria-label="Evidence" className="flex flex-wrap gap-1.5">
          {chips.shown.map((e) => (
            <li key={e.evidence_id} className="max-w-full"><EvidenceChip evidence={e} onOpen={open(e.source_ref)} /></li>
          ))}
        </ul>
        {chips.toggle}
      </div>
    )
  }
  if (view.evidence.length === 0) return <p className="text-sm text-muted-foreground">No evidence yet.</p>
  // Facts (seen in a tool's output, with a quote) first; then what the agent inferred from them.
  const groups = [
    { title: 'Facts', hint: 'seen in a source, with the exact quote', items: view.evidence.filter((e) => e.kind === 'fact') },
    { title: 'Inferences', hint: 'what the agent concluded from the facts', items: view.evidence.filter((e) => e.kind !== 'fact') },
  ].filter((g) => g.items.length > 0)
  return (
    <div aria-label="Evidence" className="flex flex-col gap-6">
      {groups.map((group) => (
        <section key={group.title} className="flex flex-col gap-2">
          <h3 className="flex items-baseline gap-2 text-sm font-semibold">
            {group.title} <span className="font-normal text-muted-foreground">{group.items.length} · {group.hint}</span>
          </h3>
          <ul className="flex flex-col gap-2">
            {group.items.map((e) => (
              <li key={e.evidence_id} className="flex flex-col gap-1.5 rounded-lg border bg-card p-3 text-sm">
                <div className="flex items-center gap-2">
                  <EvidenceChip evidence={e} onOpen={open(e.source_ref)} />
                  {e.stance !== 'neutral' && (
                    <Badge variant="outline" className={statusTone(e.stance === 'contradicts' ? 'failed' : 'ok')}>{e.stance}</Badge>
                  )}
                </div>
                <p>{e.statement}</p>
                {e.excerpt && (
                  <pre className="overflow-x-auto rounded-md border-l-2 border-primary/40 bg-muted px-3 py-2 text-[0.8125rem] whitespace-pre-wrap">{e.excerpt}</pre>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
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
    <pre aria-label="Diff" className="overflow-x-auto rounded-md border bg-muted/40 p-2 text-[0.8125rem] leading-5">
      {diff.split('\n').map((line, index) => {
        const tone = line.startsWith('+') && !line.startsWith('+++')
          ? 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-200'
          : line.startsWith('-') && !line.startsWith('---')
            ? 'bg-red-500/15 text-red-800 dark:text-red-200'
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
          <Card
            key={run.phase}
            size="sm"
            className={
              failed === undefined
                ? ''
                : failed
                  ? 'bg-red-500/5 ring-red-500/30'
                  : 'bg-emerald-500/5 ring-emerald-500/30'
            }
          >
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {failed === undefined ? (
                  <CircleDashed className="size-4 animate-spin" aria-hidden />
                ) : failed ? (
                  <XCircle className="size-4 text-red-600 dark:text-red-400" aria-hidden />
                ) : (
                  <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden />
                )}
                <span className="capitalize">{run.phase}</span>
                {r && (
                  <span className="text-[0.8125rem] font-normal text-muted-foreground">
                    {r.passed ?? 0} passed · {r.failed ?? 0} failed · {(r.duration_ms / 1000).toFixed(1)} s
                    {r.as_expected && ' · as expected'}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {r && <code className="text-[0.8125rem] text-muted-foreground">$ {r.command}</code>}
              <pre className="max-h-80 overflow-auto rounded-md bg-slate-950 p-3 text-[0.8125rem] text-emerald-200 ring-1 ring-white/10">{run.lines.join('\n')}</pre>
              {r?.sandbox_image_id && <p className="text-[0.8125rem] text-muted-foreground">Nebius Sandbox image {r.sandbox_image_id}</p>}
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
        {approve.isError && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(approve.error)}</p>}
      </div>
    </div>
  )
}

/** Follow-up questions about this investigation, saved with it. */
function ChatTab({ investigationId }: { investigationId: string }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const path = { params: { path: { investigation_id: investigationId } } }
  const history = useQuery({
    queryKey: ['messages', investigationId],
    queryFn: () =>
      ok(api.GET('/investigations/{investigation_id}/messages', { params: { path: { investigation_id: investigationId } } })),
  })
  const ask = useMutation({
    mutationFn: (content: string) =>
      ok(api.POST('/investigations/{investigation_id}/messages', { ...path, body: { content } })),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['messages', investigationId] })
      setPending(null)
    },
  })
  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (content.length < 2 || ask.isPending) return
    setPending(content)
    setDraft('')
    ask.mutate(content)
  }
  const turns = [...(history.data ?? []), ...(pending ? [{ id: 'pending', role: 'user', content: pending }] : [])]
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      {turns.map((turn) =>
        turn.role === 'user' ? (
          <div key={turn.id} className="self-end rounded-lg bg-muted px-3 py-2 text-sm">{turn.content}</div>
        ) : (
          <Markdown key={turn.id}>{turn.content}</Markdown>
        ),
      )}
      {ask.isPending && <p className="text-sm text-muted-foreground">Thinking…</p>}
      {ask.isError && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(ask.error)}</p>}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <Textarea aria-label="Follow-up question" placeholder="Which commit introduced this?" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <Button type="submit" size="sm" disabled={draft.trim().length < 2 || ask.isPending}>Ask</Button>
      </form>
    </div>
  )
}

const SPAN_BAR: Record<string, string> = {
  llm: 'bg-emerald-500',
  tool: 'bg-sky-500',
  mcp: 'bg-indigo-500',
  retrieval: 'bg-violet-500',
  memory: 'bg-fuchsia-500',
  sandbox: 'bg-amber-500',
}

/** Every model, tool, retrieval, memory and sandbox call, as a waterfall: the proof of which
 * Nemotron model on Nebius did what, and what it cost. */
function TraceTab({ investigationId, done }: { investigationId: string; done: boolean }) {
  const trace = useQuery({
    queryKey: ['trace', investigationId, done], // reloads once the run ends
    queryFn: () =>
      ok(api.GET('/investigations/{investigation_id}/trace', { params: { path: { investigation_id: investigationId } } })),
  })
  if (trace.isError) return <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(trace.error)}</p>
  if (!trace.data) return <p className="text-sm text-muted-foreground">Loading…</p>
  const { spans, totals } = trace.data
  if (spans.length === 0) return <p className="text-sm text-muted-foreground">No calls recorded yet.</p>
  const start = Date.parse(spans[0].started_at)
  const total = Math.max(totals.duration_ms, 1)
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        {totals.spans} calls · {totals.llm_calls} to Nemotron ·{' '}
        {(totals.prompt_tokens + totals.completion_tokens).toLocaleString()} tokens · ${totals.cost_usd.toFixed(4)} ·{' '}
        {(totals.duration_ms / 1000).toFixed(0)} s
      </p>
      <table aria-label="Trace" className="w-full text-[0.8125rem]">
        <tbody>
          {spans.map((span) => {
            const left = ((Date.parse(span.started_at) - start) / total) * 100
            const width = Math.max(((span.latency_ms ?? 0) / total) * 100, 0.5)
            return (
              <tr key={span.id} title={span.error ?? span.model ?? undefined}>
                <td className="w-0 py-0.5 pr-2 whitespace-nowrap">
                  <Badge variant="outline" className="h-5 px-1.5 text-xs">{span.kind}</Badge>{' '}
                  <span className={span.status === 'ok' ? '' : 'text-destructive'}>{span.name}</span>
                  {span.tier && <span className="text-muted-foreground"> · {span.tier}</span>}
                  {span.downgraded && <span className="text-muted-foreground"> (fell back to Super)</span>}
                </td>
                <td className="py-0.5">
                  <div className="relative h-2.5 rounded bg-muted/50">
                    <div
                      className={`absolute h-2.5 rounded ${SPAN_BAR[span.kind] ?? 'bg-muted-foreground/60'}`}
                      style={{ left: `${Math.min(left, 99.5)}%`, width: `${width}%` }}
                    />
                  </div>
                </td>
                <td className="w-0 py-0.5 pl-2 text-right whitespace-nowrap text-muted-foreground">
                  {span.latency_ms != null ? `${(span.latency_ms / 1000).toFixed(1)} s` : '—'}
                  {span.cost_usd > 0 && ` · $${span.cost_usd.toFixed(4)}`}
                  {span.cached && ' · cached'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={statusTone(status)}>
      {status.replace('_', ' ')}
    </Badge>
  )
}

const pct = (value: number) => `${Math.round(value * 100)}%`

function errorMessage(error: Error): string {
  if (error instanceof ApiError) return error.message
  return "Can't reach the server. Check your connection and try again."
}
