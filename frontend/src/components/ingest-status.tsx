import type { ComponentProps } from 'react'

import type { Schemas } from '@/api/types'
import { Badge } from '@/components/ui/badge'

type BadgeVariant = ComponentProps<typeof Badge>['variant']

const INGEST: Record<Schemas['IngestStatus'], { label: string; variant: BadgeVariant }> = {
  not_indexed: { label: 'Not indexed yet', variant: 'outline' },
  queued: { label: 'Waiting to index', variant: 'outline' },
  running: { label: 'Indexing…', variant: 'secondary' },
  indexed: { label: 'Indexed', variant: 'secondary' },
  failed: { label: 'Indexing failed', variant: 'destructive' },
}

/** A repository's indexing status, as a badge. */
export function IngestBadge({ status }: { status: Schemas['IngestStatus'] }) {
  const { label, variant } = INGEST[status]
  return <Badge variant={variant}>{label}</Badge>
}
