// Folds an investigation's events into what the Investigation page shows. Pure, so the page
// renders the same thing live and from a replay of saved events.
import type { EventOf, EventType, InvestigationEvent, ModelTier } from '@/api/types'

type P<T extends EventType> = EventOf<T>['payload']

export type StepState = {
  step: string
  status: 'running' | 'done' | 'skipped'
  tier?: ModelTier | null
  durationMs?: number
  summary?: string | null
}

export type ToolCallState = P<'tool_call'> & { result?: P<'tool_result'> }

export type TestRun = { phase: string; lines: string[]; result?: P<'test_completed'> }

export type InvestigationView = {
  lastSeq: number
  steps: StepState[] // in the order they started
  tools: ToolCallState[]
  evidence: P<'evidence_added'>[]
  hypotheses: P<'hypothesis'>[]
  verdicts: P<'verdict'>[]
  confidence?: P<'confidence'>
  patch?: P<'patch_proposed'>
  patchStatus?: P<'patch_status'>
  tests: TestRun[] // baseline first, then patched
  pr?: P<'pr_draft'>
  prUrl?: string
  report?: P<'report'>
  errors: P<'error'>[]
  done?: P<'done'>
}

export const emptyView = (): InvestigationView => ({
  lastSeq: 0,
  steps: [],
  tools: [],
  evidence: [],
  hypotheses: [],
  verdicts: [],
  tests: [],
  errors: [],
})

/** Adds `events` to `view` (a new object; `view` is not changed). Events at or before
 * `view.lastSeq` are ignored, so overlapping polls are harmless. */
export function applyEvents(view: InvestigationView, events: InvestigationEvent[]): InvestigationView {
  const next: InvestigationView = {
    ...view,
    steps: [...view.steps],
    tools: [...view.tools],
    evidence: [...view.evidence],
    hypotheses: [...view.hypotheses],
    verdicts: [...view.verdicts],
    tests: view.tests.map((run) => ({ ...run, lines: [...run.lines] })),
    errors: [...view.errors],
  }
  for (const event of events) {
    if (event.seq <= next.lastSeq) continue
    next.lastSeq = event.seq
    apply(next, event)
  }
  return next
}

function apply(view: InvestigationView, event: InvestigationEvent) {
  switch (event.type) {
    case 'step_started': {
      const { step, tier } = event.payload
      view.steps.push({ step, status: 'running', tier })
      break
    }
    case 'step_completed': {
      const { step, duration_ms, summary, skipped } = event.payload
      const index = view.steps.findLastIndex((s) => s.step === step && s.status === 'running')
      const done: StepState = {
        ...(index >= 0 ? view.steps[index] : { step }),
        step,
        status: skipped ? 'skipped' : 'done',
        durationMs: duration_ms,
        summary,
      }
      if (index >= 0) view.steps[index] = done
      else view.steps.push(done)
      break
    }
    case 'tool_call':
      view.tools.push({ ...event.payload })
      break
    case 'tool_result': {
      const call = view.tools.find((t) => t.call_id === event.payload.call_id)
      if (call) call.result = event.payload
      break
    }
    case 'evidence_added':
      view.evidence.push(event.payload)
      break
    case 'hypothesis':
      view.hypotheses.push(event.payload)
      break
    case 'verdict':
      view.verdicts.push(event.payload)
      break
    case 'confidence':
      view.confidence = event.payload
      break
    case 'patch_proposed':
      view.patch = event.payload
      view.tests = [] // a new attempt is tested from scratch
      break
    case 'patch_status':
      view.patchStatus = event.payload
      break
    case 'test_output':
      testRun(view, event.payload.phase).lines.push(event.payload.line)
      break
    case 'test_completed':
      testRun(view, event.payload.phase).result = event.payload
      break
    case 'pr_draft':
      view.pr = event.payload
      break
    case 'pr_opened':
      view.prUrl = event.payload.url
      break
    case 'report':
      view.report = event.payload
      break
    case 'error':
      view.errors.push(event.payload)
      break
    case 'done':
      view.done = event.payload
      break
  }
}

function testRun(view: InvestigationView, phase: string): TestRun {
  let run = view.tests.find((r) => r.phase === phase)
  if (!run) {
    run = { phase, lines: [] }
    view.tests.push(run)
  }
  return run
}

/** "write_report" -> "Write report" */
export const stepLabel = (step: string) => step.charAt(0).toUpperCase() + step.slice(1).replace(/_/g, ' ')
