// One palette for meaning, used across pages: each source kind, status and metric keeps its
// hue everywhere, so a colour can be read at a glance. Tinted backgrounds (the hue at ~12%) with
// a darker text in light mode and a lighter one in dark mode keep contrast in both themes.
import type { SourceKind } from '@/lib/source-refs'

export const SOURCE_TONE: Record<SourceKind, string> = {
  code: 'border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300',
  commit: 'border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-300',
  log: 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300',
  doc: 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  incident: 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300',
  issue: 'border-teal-500/30 bg-teal-500/10 text-teal-700 dark:text-teal-300',
  memory: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-300',
  database: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300',
}

export const INFERENCE_TONE = 'border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300'

/** Investigation and patch statuses. */
export function statusTone(status: string): string {
  if (['completed', 'verified', 'supported', 'ok'].includes(status))
    return 'border-emerald-500/30 bg-emerald-500/12 text-emerald-700 dark:text-emerald-300'
  if (['failed', 'tests_failed', 'refuted', 'rejected', 'error'].includes(status))
    return 'border-red-500/30 bg-red-500/12 text-red-700 dark:text-red-300'
  if (['awaiting_approval', 'weak', 'untested', 'proposed'].includes(status))
    return 'border-amber-500/30 bg-amber-500/12 text-amber-800 dark:text-amber-300'
  return 'border-sky-500/30 bg-sky-500/12 text-sky-700 dark:text-sky-300' // queued, running…
}

/** Stat tiles: a different hue per metric, for the icon's tinted circle and the number. */
export const TILE_TONES = {
  emerald: { icon: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300', value: 'text-emerald-700 dark:text-emerald-300' },
  sky: { icon: 'bg-sky-500/15 text-sky-600 dark:text-sky-300', value: 'text-sky-700 dark:text-sky-300' },
  violet: { icon: 'bg-violet-500/15 text-violet-600 dark:text-violet-300', value: 'text-violet-700 dark:text-violet-300' },
  amber: { icon: 'bg-amber-500/15 text-amber-600 dark:text-amber-300', value: 'text-amber-700 dark:text-amber-300' },
  rose: { icon: 'bg-rose-500/15 text-rose-600 dark:text-rose-300', value: 'text-rose-700 dark:text-rose-300' },
  teal: { icon: 'bg-teal-500/15 text-teal-600 dark:text-teal-300', value: 'text-teal-700 dark:text-teal-300' },
} as const

export type TileTone = keyof typeof TILE_TONES
