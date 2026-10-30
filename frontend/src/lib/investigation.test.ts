import { describe, expect, it } from 'vitest'

import type { InvestigationEvent } from '@/api/types'
import events from '@/test/fixtures/scenario-1-events.json'

import { applyEvents, emptyView } from './investigation'

const all = events as unknown as InvestigationEvent[]

describe('applyEvents', () => {
  it('folds the scenario-1 replay into a finished, red-then-green investigation', () => {
    const view = applyEvents(emptyView(), all)

    expect(view.done?.status).toBe('completed')
    expect(view.steps.every((s) => s.status !== 'running')).toBe(true)
    expect(view.tools.every((t) => t.result)).toBe(true)
    expect(view.report?.fix).toBe('verified')
    expect(view.tests.map((r) => [r.phase, r.result?.as_expected])).toEqual([
      ['baseline', true],
      ['patched', true],
    ])
  })

  it('gives the same view when events arrive in overlapping polls', () => {
    const half = Math.floor(all.length / 2)
    const once = applyEvents(emptyView(), all)
    const twice = applyEvents(applyEvents(emptyView(), all.slice(0, half + 5)), all.slice(half))
    expect(twice).toEqual(once)
  })
})
