import type { ComponentProps } from 'react'

import type { Schemas } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { statusTone } from '@/lib/colors'

type BadgeVariant = ComponentProps<typeof Badge>['variant']

const INGEST: Record<Schemas['IngestStatus'], { label: string; variant: BadgeVariant }> = {
  not_indexed: { label: 'Not indexed yet', variant: 'outline' },
  queued: { label: 'Waiting to index', variant: 'outline' },
  running: { label: 'Indexing…', variant: 'secondary' },
  indexed: { label: 'Indexed', variant: 'secondary' },
  failed: { label: 'Indexing failed', variant: 'destructive' },
}

// The shared status colours: indexed green, waiting amber, running blue, failed red.
const TONE: Record<Schemas['IngestStatus'], string> = {
  not_indexed: 'proposed',
  queued: 'proposed',
  running: 'running',
  indexed: 'completed',
  failed: 'failed',
}

/** A repository's indexing status, as a badge. */
export function IngestBadge({ status }: { status: Schemas['IngestStatus'] }) {
  const { label, variant } = INGEST[status]
  return (
    <Badge variant={variant} className={statusTone(TONE[status])}>
      {label}
    </Badge>
  )
}
