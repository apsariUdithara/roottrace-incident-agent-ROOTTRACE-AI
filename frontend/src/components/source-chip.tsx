import {
  Brain,
  CircleDot,
  Database,
  FileCode,
  FileText,
  GitCommitHorizontal,
  Lightbulb,
  ScrollText,
  Siren,
  type LucideIcon,
} from 'lucide-react'

import type { EventOf, Schemas } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { describeRef, type SourceKind } from '@/lib/source-refs'

export const SOURCE_ICONS: Record<SourceKind, LucideIcon> = {
  doc: FileText,
  code: FileCode,
  issue: CircleDot,
  incident: Siren,
  commit: GitCommitHorizontal,
  log: ScrollText,
  memory: Brain,
  database: Database,
}

/** Where a claim comes from: its label (F1, S1), its kind's icon and a short form of the ref,
 * with the whole ref as the tooltip. With `onOpen` it's a button that opens the source viewer;
 * a ref the viewer can't open (query results) never is. */
export function SourceChip({
  sourceRef,
  kind,
  label,
  onOpen,
}: {
  sourceRef: string
  kind?: SourceKind | null // when the caller knows it better than the ref does
  label?: string
  onOpen?: () => void
}) {
  const summary = describeRef(sourceRef)
  const Icon = SOURCE_ICONS[kind ?? summary.kind ?? 'doc']
  const content = (
    <>
      <Icon data-icon="inline-start" aria-hidden />
      {label && <span className="font-semibold">{label}</span>}
      <span className="truncate">{summary.short}</span>
    </>
  )
  if (onOpen && summary.opens) {
    return (
      <Badge
        variant="outline"
        title={sourceRef}
        className="max-w-full cursor-pointer hover:bg-muted"
        render={<button type="button" onClick={onOpen} />}
      >
        {content}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" title={sourceRef} className="max-w-full">
      {content}
    </Badge>
  )
}

type Evidence = EventOf<'evidence_added'>['payload']

/** A piece of evidence: a fact by its source, an inference by its label (it rests on facts,
 * which have their own chips). */
export function EvidenceChip({ evidence, onOpen }: { evidence: Evidence; onOpen?: () => void }) {
  if (evidence.kind === 'fact' && evidence.source_ref) {
    return (
      <SourceChip
        sourceRef={evidence.source_ref}
        kind={evidence.source_type}
        label={evidence.label}
        onOpen={onOpen}
      />
    )
  }
  return (
    <Badge variant="outline" title={evidence.statement} className="max-w-full">
      <Lightbulb data-icon="inline-start" aria-hidden />
      <span className="font-semibold">{evidence.label}</span>
      <span className="truncate">inference</span>
    </Badge>
  )
}

/** A source a chat answer cites, by the label the answer writes ([S1]). It opens the search's
 * own ref, which names the exact section or lines. */
export function CitationChip({
  citation,
  onOpen,
}: {
  citation: Schemas['ChatCitation']
  onOpen?: () => void
}) {
  return (
    <SourceChip
      sourceRef={citation.ref}
      kind={citation.source_type}
      label={citation.label}
      onOpen={onOpen}
    />
  )
}
