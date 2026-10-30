// /evaluation: what `eval/evaluate.py` measured. Every number here comes from a saved run, never
// typed by hand; failed cases are shown, not hidden.
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2,
  FileSearch,
  HeartPulse,
  Quote,
  ShieldCheck,
  Target,
  Wrench,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { api, ok } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { StatTile } from '@/components/stat-tile'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { statusTone, type TileTone } from '@/lib/colors'
import { errorMessage } from '@/lib/errors'
import { formatUtc } from '@/lib/time'

/** The headline metrics, in the README's order. Rates are 0–1. */
const TILES: { key: string; label: string; hint: string; icon: LucideIcon; tone: TileTone }[] = [
  { key: 'root_cause_accuracy', label: 'Root cause correct', hint: 'judged against the expected cause', icon: Target, tone: 'emerald' },
  { key: 'citations_valid', label: 'Citations valid', hint: 'quotes found verbatim in the source', icon: Quote, tone: 'sky' },
  { key: 'verified_fix_rate', label: 'Fix passes hidden test', hint: 'a test the agent never saw', icon: ShieldCheck, tone: 'teal' },
  { key: 'evidence_recall', label: 'Expected evidence cited', hint: 'share of the evidence that matters', icon: FileSearch, tone: 'violet' },
  { key: 'tool_selection', label: 'Right tools used', hint: 'expected tools ⊆ tools called', icon: Wrench, tone: 'amber' },
  { key: 'no_false_fix_rate', label: 'No fix on healthy code', hint: 'no patch when nothing is broken', icon: HeartPulse, tone: 'rose' },
]

type Case = Record<string, unknown>

export function EvaluationPage() {
  const runs = useQuery({ queryKey: ['evaluation', 'runs'], queryFn: () => ok(api.GET('/evaluation/runs')) })
  const [chosen, setChosen] = useState<string | null>(null)
  const runId = chosen ?? runs.data?.[0]?.id

  const run = useQuery({
    queryKey: ['evaluation', 'runs', runId],
    queryFn: () => ok(api.GET('/evaluation/runs/{run_id}', { params: { path: { run_id: runId! } } })),
    enabled: !!runId,
  })

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <PageHeader
        title="Evaluation"
        description="8 incident scenarios × 2 phrasings, plus healthy versions, run end to end by eval/evaluate.py. Fixes are graded by hidden tests the agent never sees."
      />
      {runs.isPending && <Skeleton className="h-24" />}
      {runs.isError && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(runs.error)}</p>}
      {runs.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No runs yet. Run <code>python eval/evaluate.py</code>.</p>
      )}

      {runs.data && runs.data.length > 0 && (
        <NativeSelect
          size="sm"
          aria-label="Run"
          value={runId}
          onChange={(event) => setChosen(event.target.value)}
          className="w-fit"
        >
          {runs.data.map((r, index) => (
            <NativeSelectOption key={r.id} value={r.id}>
              {formatUtc(r.started_at)} · {r.metrics.cases ?? '?'} cases{index === 0 ? ' (latest)' : ''}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      )}

      {run.data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {TILES.filter((t) => t.key in run.data.metrics).map((tile) => (
              <StatTile
                key={tile.key}
                label={tile.label}
                value={pct(run.data.metrics[tile.key])}
                hint={tile.hint}
                icon={tile.icon}
                tone={tile.tone}
              />
            ))}
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            {run.data.metrics.cases} cases · ${run.data.metrics.total_cost_usd?.toFixed(2)} in total ·{' '}
            {Math.round(run.data.metrics.avg_seconds ?? 0)} s per investigation on average · commit{' '}
            <code>{run.data.git_sha?.slice(0, 7) ?? 'unknown'}</code>
          </p>
          <Cases cases={run.data.cases} />
          <Card size="sm">
            <CardHeader>
              <CardTitle>Models</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="text-[0.8125rem]">
                {Object.entries(run.data.models).map(([tier, model]) => (
                  <li key={tier}>
                    <span className="capitalize">{tier}</span>: <code>{String(model)}</code>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </>
      )}
      {run.isError && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(run.error)}</p>}
    </div>
  )
}

/** A case failed if any of its checks did; a healthy-code case fails by proposing any fix. */
const failedCase = (c: Case) =>
  c.root_cause_correct === false ||
  c.citations === false ||
  c.fix === false ||
  (String(c.id).startsWith('healthy') && !!c.fix_status && c.fix_status !== 'none')

const FIX_LABEL: Record<string, string> = { verified: 'verified', tests_failed: 'failed', none: 'none', untested: 'untested' }

function Cases({ cases }: { cases: Case[] }) {
  const [onlyFailures, setOnlyFailures] = useState(false)
  const failures = cases.filter(failedCase).length
  const shown = onlyFailures ? cases.filter(failedCase) : cases
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-base font-semibold">Per-case results</h2>
        <Button size="sm" variant={onlyFailures ? 'outline' : 'secondary'} onClick={() => setOnlyFailures(false)}>
          All {cases.length}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className={onlyFailures ? statusTone('failed') : ''}
          onClick={() => setOnlyFailures(true)}
        >
          Failures {failures}
        </Button>
      </div>
    <div className="overflow-x-auto rounded-xl bg-card ring-1 ring-foreground/10">
      <table aria-label="Cases" className="w-full text-[0.8125rem]">
        <thead className="border-b text-left text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Case</th>
            <th className="px-3 py-2">Root cause</th>
            <th className="px-3 py-2">Citations</th>
            <th className="px-3 py-2">Evidence</th>
            <th className="px-3 py-2">Fix</th>
            <th className="px-3 py-2 text-right">Confidence</th>
            <th className="px-3 py-2 text-right">Cost · time</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {shown.map((c) => (
            <tr key={String(c.id)} className="align-top">
              <td className="px-3 py-1.5 font-mono whitespace-nowrap">
                {c.investigation_id ? (
                  <Link to={`/investigations/${String(c.investigation_id)}`} className="text-primary hover:underline">
                    {String(c.id)}
                  </Link>
                ) : (
                  String(c.id)
                )}
              </td>
              <td className="px-3 py-1.5" title={c.judge_reason ? String(c.judge_reason) : undefined}>
                <Mark value={c.root_cause_correct} />
              </td>
              <td className="px-3 py-1.5"><Mark value={c.citations} /></td>
              <td className="px-3 py-1.5" title={missing(c.evidence_missing)}>
                {typeof c.evidence === 'number' ? pct(c.evidence) : '—'}
              </td>
              <td className="px-3 py-1.5 whitespace-nowrap">
                <Mark value={c.fix} /> {c.fix_status ? (FIX_LABEL[String(c.fix_status)] ?? String(c.fix_status)) : ''}
              </td>
              <td className="px-3 py-1.5 text-right">{typeof c.confidence === 'number' ? pct(c.confidence) : '—'}</td>
              <td className="px-3 py-1.5 text-right whitespace-nowrap text-muted-foreground">
                {typeof c.cost_usd === 'number' ? `$${c.cost_usd.toFixed(3)}` : '—'} · {String(c.seconds ?? '—')} s
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </div>
  )
}

/** ✓ / ✗ for a pass or fail; a dash where the check doesn't apply (e.g. no fix on healthy code). */
function Mark({ value }: { value: unknown }) {
  if (value === true) return <CheckCircle2 className="inline size-4 text-emerald-600 dark:text-emerald-400" aria-label="pass" />
  if (value === false) return <XCircle className="inline size-4 text-red-600 dark:text-red-400" aria-label="fail" />
  return <span className="text-muted-foreground">—</span>
}

const missing = (refs: unknown) =>
  Array.isArray(refs) && refs.length ? `Not cited: ${refs.join(', ')}` : undefined

// null: the run had no cases for it (e.g. no healthy versions, so no no-false-fix rate).
const pct = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value * 100)}%`)
