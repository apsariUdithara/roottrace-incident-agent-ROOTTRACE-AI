/** "2026-10-06 14:26:27", in UTC: times next to logs and evidence, which are UTC too. */
export function formatUtc(iso: string): string {
  return new Date(iso).toISOString().slice(0, 19).replace('T', ' ')
}

/** "Oct 25, 04:11 UTC" (or "Aug 19, 2026" for a date with no time): short, for lists. */
export function formatShort(iso: string): string {
  const d = new Date(iso)
  const midnight = d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0
  if (midnight) return d.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
  return `${d.toLocaleString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })} UTC`
}
