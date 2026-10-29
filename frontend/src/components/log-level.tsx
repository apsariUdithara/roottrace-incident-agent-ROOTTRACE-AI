import { Badge } from '@/components/ui/badge'

const ERROR_LEVELS = new Set(['ERROR', 'FATAL', 'CRITICAL'])
const WARN_LEVELS = new Set(['WARN', 'WARNING'])

/** A log line's level, as logged; errors and warnings stand out. */
export function LevelBadge({ level }: { level: string }) {
  const upper = level.toUpperCase()
  if (ERROR_LEVELS.has(upper)) return <Badge variant="destructive">{level}</Badge>
  if (WARN_LEVELS.has(upper)) return <Badge variant="secondary">{level}</Badge>
  return <span className="px-2 text-muted-foreground">{level}</span>
}
