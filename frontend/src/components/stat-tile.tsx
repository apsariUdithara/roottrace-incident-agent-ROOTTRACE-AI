import type { LucideIcon } from 'lucide-react'

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { TILE_TONES, type TileTone } from '@/lib/colors'

/** A headline number with its icon in a tinted circle; each metric keeps its own hue. */
export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  tone,
}: {
  label: string
  value: string
  hint?: string
  icon: LucideIcon
  tone: TileTone
}) {
  const colors = TILE_TONES[tone]
  return (
    <Card size="sm">
      <CardHeader className="grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-1">
          <CardDescription>{label}</CardDescription>
          <CardTitle className={`font-sans text-3xl font-bold tabular-nums ${colors.value}`}>{value}</CardTitle>
          {hint && <p className="text-[0.8125rem] text-muted-foreground">{hint}</p>}
        </div>
        <span className={`flex size-10 items-center justify-center rounded-full ${colors.icon}`}>
          <Icon className="size-5" aria-hidden />
        </span>
      </CardHeader>
    </Card>
  )
}
