import { CircleDot, FileCode, FileText, Siren, type LucideIcon } from 'lucide-react'

import type { Schemas } from '@/api/types'
import { Badge } from '@/components/ui/badge'

export const SOURCE_ICONS: Record<Schemas['DocumentSourceType'], LucideIcon> = {
  doc: FileText,
  code: FileCode,
  issue: CircleDot,
  incident: Siren,
}

/** A source an answer cites: its label as the answer writes it ([S1]), and where it's from. */
export function CitationChip({ citation }: { citation: Schemas['ChatCitation'] }) {
  const Icon = SOURCE_ICONS[citation.source_type]
  return (
    <Badge variant="outline" title={citation.ref} className="max-w-full">
      <Icon data-icon="inline-start" aria-hidden />
      <span className="font-semibold">{citation.label}</span>
      <span className="truncate">{citation.ref}</span>
    </Badge>
  )
}
