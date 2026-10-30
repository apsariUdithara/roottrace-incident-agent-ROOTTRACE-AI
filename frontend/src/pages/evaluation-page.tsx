// /evaluation: what `eval/evaluate.py` measured. Every number here comes from a saved run, never
// typed by hand; failed cases are shown, not hidden.
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { api, ok } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/errors'
import { formatUtc } from '@/lib/time'

/** The headline metrics, in the README's order. Rates are 0–1. */
const TILES: { key: string; label: string; hint: string }[] = [
  { key: 'root_cause_accuracy', label: 'Root cause correct', hint: 'judged against the expected cause' },
  { key: 'citations_valid', label: 'Citations valid', hint: 'quotes found verbatim in the source' },
  { key: 'verified_fix_rate', label: 'Fix passes hidden test', hint: 'a test the agent never saw' },
  { key: 'evidence_recall', label: 'Expected evidence cited', hint: 'share of the evidence that matters' },
  { key: 'tool_selection', label: 'Right tools used', hint: 'expected tools ⊆ tools called' },
  { key: 'no_false_fix_rate', label: 'No fix on healthy code', hint: 'no patch when nothing is broken' },
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
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {TILES.filter((t) => t.key in run.data.metrics).map((tile) => (
              <Card key={tile.key} size="sm">
                <CardHeader>
                  <CardDescription>{tile.label}</CardDescription>
                  <CardTitle className="text-2xl">{pct(run.data.metrics[tile.key])}</CardTitle>
                  <p className="text-[0.8125rem] text-muted-foreground">{tile.hint}</p>
                </CardHeader>
              </Card>
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

function Cases({ cases }: { cases: Case[] }) {
  return (
    <div className="overflow-x-auto rounded-lg ring-1 ring-foreground/10">
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
          {cases.map((c) => (
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
                <Mark value={c.fix} /> {c.fix_status ? String(c.fix_status) : ''}
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
  )
}

/** ✓ / ✗ for a pass or fail; a dash where the check doesn't apply (e.g. no fix on healthy code). */
function Mark({ value }: { value: unknown }) {
  if (value === true) return <CheckCircle2 className="inline size-4 text-primary" aria-label="pass" />
  if (value === false) return <XCircle className="inline size-4 text-destructive" aria-label="fail" />
  return <span className="text-muted-foreground">—</span>
}

const missing = (refs: unknown) =>
  Array.isArray(refs) && refs.length ? `Not cited: ${refs.join(', ')}` : undefined

// null: the run had no cases for it (e.g. no healthy versions, so no no-false-fix rate).
const pct = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value * 100)}%`)
