// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventOf } from '@/api/types'
import { CitationChip, EvidenceChip, SourceChip } from '@/components/source-chip'

type Evidence = EventOf<'evidence_added'>['payload']

function evidence(fields: Partial<Evidence>): Evidence {
  return {
    evidence_id: 'e1',
    label: 'F1',
    kind: 'fact',
    statement: 'The pool was raised to 100.',
    tool: 'get_commit_diff',
    source_type: 'commit',
    source_ref: 'commit 5b0bb55c90',
    excerpt: '+DB_POOL_SIZE = 100',
    cites: [],
    stance: 'neutral',
    weight: 1,
    hypothesis_id: null,
    ...fields,
  }
}

afterEach(cleanup)

describe('SourceChip', () => {
  it('shows its label and a short form of the ref, with the whole ref as the tooltip', () => {
    render(<SourceChip label="F4" sourceRef="src/paystream/database/config.py L1-12 @ v1.4.8" />)

    const chip = screen.getByTitle('src/paystream/database/config.py L1-12 @ v1.4.8')
    expect(chip.textContent).toBe('F4config.py L1-12')
    expect(chip.querySelector('svg')).not.toBeNull() // its kind's icon
    expect(screen.queryByRole('button')).toBeNull() // nothing to open it with
  })

  it('is a button that opens the source, when it can be opened', async () => {
    const onOpen = vi.fn()
    render(<SourceChip label="F3" sourceRef="commit 5b0bb55c90" onOpen={onOpen} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'F35b0bb55' }))

    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('is never a button for query results, which are not kept', () => {
    render(<SourceChip label="F7" sourceRef="sql: SELECT 1" onOpen={vi.fn()} />)

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByTitle('sql: SELECT 1').textContent).toBe('F7SQL query')
  })

  it("uses the caller's kind for the icon when the ref doesn't say", () => {
    // A Markdown path reads as a doc, unless the evidence says it was read as code.
    const { container: guessed } = render(<SourceChip sourceRef="README.md L1-3 @ 6c4a9a3" />)
    const { container: told } = render(<SourceChip sourceRef="README.md L1-3 @ 6c4a9a3" kind="code" />)

    expect(guessed.querySelector('svg')?.getAttribute('class')).toContain('lucide-file-text')
    expect(told.querySelector('svg')?.getAttribute('class')).toContain('lucide-file-code')
  })
})

describe('EvidenceChip', () => {
  it('shows a fact by its source, and opens it', async () => {
    const onOpen = vi.fn()
    render(<EvidenceChip evidence={evidence({})} onOpen={onOpen} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'F15b0bb55' }))

    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('shows an inference by its label, with its statement as the tooltip', () => {
    const inference = evidence({
      label: 'I1',
      kind: 'inference',
      statement: 'Five instances at 100 exceed max_connections.',
      tool: null,
      source_type: null,
      source_ref: null,
      excerpt: null,
      cites: ['e1'],
    })
    render(<EvidenceChip evidence={inference} onOpen={vi.fn()} />)

    expect(screen.getByTitle('Five instances at 100 exceed max_connections.').textContent).toBe('I1inference')
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('CitationChip', () => {
  it("opens the search's own ref, which names the section", async () => {
    const onOpen = vi.fn()
    const citation = {
      label: 'S1',
      source_type: 'doc' as const,
      path: 'docs/database.md',
      ref: 'docs/database.md § Database > Connection pooling L5-17 @ 6c4a9a3',
      source_ref: 'doc:docs/database.md',
    }
    render(<CitationChip citation={citation} onOpen={onOpen} />)

    const chip = screen.getByRole('button', { name: 'S1database.md § Connection pooling' })
    expect(chip.getAttribute('title')).toBe(citation.ref)
    await userEvent.setup().click(chip)
    expect(onOpen).toHaveBeenCalledOnce()
  })
})
