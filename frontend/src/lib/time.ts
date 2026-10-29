/** "2026-10-06 14:26:27", in UTC: times next to logs and evidence, which are UTC too. */
export function formatUtc(iso: string): string {
  return new Date(iso).toISOString().slice(0, 19).replace('T', ' ')
}
