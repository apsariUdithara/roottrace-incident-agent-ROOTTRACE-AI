import { describe, expect, it } from 'vitest'

import type { EventOf, InvestigationEvent } from '@/api/types'
import { describeRef, headingSlug } from '@/lib/source-refs'
import fixture from '@/test/fixtures/scenario-1-events.json'

const EVENTS = fixture as InvestigationEvent[]
const FACTS = EVENTS.filter(
  (event): event is EventOf<'evidence_added'> =>
    event.type === 'evidence_added' && event.payload.kind === 'fact',
).map((event) => event.payload)

describe('describeRef', () => {
  it.each([
    // The app's refs: Knowledge items and chat.
    ['file:app/db.py#L10-L20@f89f6df0a1b2', 'code', 'db.py L10-20'],
    ['file:app/db.py#L3-L3@f89f6df0a1b2', 'code', 'db.py L3'],
    ['file:app/db.py@HEAD', 'code', 'db.py'],
    ['doc:docs/pool.md', 'doc', 'pool.md'],
    ['doc:docs/pool.md#pool-sizing', 'doc', 'pool.md'],
    ['incident:inc-017', 'incident', 'INC-017'],
    ['issue:42', 'issue', '#42'],
    ['commit:a3f9c21d00', 'commit', 'a3f9c21'],
    ['log:4ecb5ffc3e4ec068', 'log', 'trace 4ecb5ffc3e4ec068'],
    ['memory:0b6c2a8e-1111-4c4c-9c9c-123456789abc', 'memory', 'memory'],
    // The agent's: its tools' refs.
    ['src/paystream/database/config.py L1-12 @ v1.4.8', 'code', 'config.py L1-12'],
    ['docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3', 'doc', 'database.md § Connection pooling'],
    ['app/db.py :: connect L3-9 @ 6c4a9a3', 'code', 'db.py :: connect L3-9'],
    ['commit 5b0bb55c90', 'commit', '5b0bb55'],
    ['INC-017', 'incident', 'INC-017'],
    ['log trace_id=5a07979ff653e026', 'log', 'trace 5a07979ff653e026'],
    ['log deploy at 2026-10-06T14:20:00+00:00', 'log', 'deploy 14:20 UTC'],
    ['README.md', 'doc', 'README.md'],
  ])('reads %s', (ref, kind, short) => {
    expect(describeRef(ref)).toEqual({ kind, short, opens: true })
  })

  it("knows query results don't open, and doesn't guess at refs it doesn't know", () => {
    expect(describeRef('sql: SELECT count(*) FROM payments')).toEqual({
      kind: 'database',
      short: 'SQL query',
      opens: false,
    })
    expect(describeRef('investigation:42')).toEqual({ kind: null, short: 'investigation:42', opens: false })
  })

  it('reads every fact of a real run as its own source type', () => {
    expect(FACTS.length).toBeGreaterThan(5)
    for (const fact of FACTS) {
      const summary = describeRef(fact.source_ref!)
      expect(summary.kind, fact.source_ref!).toBe(fact.source_type)
      expect(summary.opens).toBe(fact.source_type !== 'database')
      expect(summary.short.length).toBeLessThanOrEqual(60) // short enough for a chip
    }
  })
})

describe('headingSlug', () => {
  it("makes GitHub's anchors, as the backend does", () => {
    expect(headingSlug('Connection pooling')).toBe('connection-pooling')
    expect(headingSlug('  Pool: size & limits!  ')).toBe('pool-size--limits')
    expect(headingSlug('DB_POOL_SIZE')).toBe('db_pool_size')
  })
})
