// What a `source_ref` points to, read without asking the backend: for chips, which show its kind
// and a short form. Both forms are read, as GET /projects/{id}/sources does
// (backend/app/rag/sources.py):
// - the app's, from the Knowledge list and chat: "file:app/db.py#L10-L20@<sha>", "doc:docs/a.md",
//   "incident:INC-017", "issue:42", "commit:<sha>", "log:<trace id>", "memory:<id>";
// - the agent's (its tools' refs): "app/db.py L10-20 @ v1.4.8", "docs/a.md § A > B L5-17 @ <sha>",
//   "app/db.py :: connect L3-9 @ <sha>", "commit 5b0bb55c90", "INC-017",
//   "log trace_id=4ecb5ffc3e4ec068", "log deploy at 2026-10-06T14:20:00+00:00", "sql: SELECT …".
import type { Schemas } from '@/api/types'

export type SourceKind = Schemas['EvidenceSourceType']

export type RefSummary = {
  kind: SourceKind | null // null: not a ref we know
  short: string // e.g. "config.py L1-12", "database.md § Connection pooling", "5b0bb55"
  opens: boolean // whether the source viewer can open it (query results aren't kept)
}

const APP_FILE = /^file:([^#@]+)(?:#L(\d+)(?:-L(\d+))?)?(?:@(.+))?$/
const APP_DOC = /^doc:([^#@]+)(?:#([^@]+))?(?:@(.+))?$/
const CITATION = /^(.+?)(?: § (.+?)| :: (.+?))?(?: L(\d+)-(\d+))?(?: @ (\S+))?$/
const INCIDENT_KEY = /^[A-Za-z]+-\d+$/
const DOC_SUFFIX = /\.(md|markdown)$/i

const fileName = (path: string) => path.split('/').at(-1) || path
const lines = (start?: string, end?: string) =>
  start ? ` L${start}${end && end !== start ? `-${end}` : ''}` : ''

export function describeRef(raw: string): RefSummary {
  const ref = raw.trim()
  if (ref.startsWith('sql:')) return { kind: 'database', short: 'SQL query', opens: false }

  let match = APP_FILE.exec(ref)
  if (match) return found('code', `${fileName(match[1])}${lines(match[2], match[3] ?? match[2])}`)
  match = APP_DOC.exec(ref)
  if (match) return found('doc', fileName(match[1]))

  for (const prefix of ['commit:', 'commit ']) {
    if (ref.startsWith(prefix)) return found('commit', ref.slice(prefix.length).trim().slice(0, 7))
  }
  if (ref.startsWith('incident:')) return found('incident', ref.slice(9).trim().toUpperCase())
  if (ref.startsWith('issue:')) return found('issue', `#${ref.slice(6).trim().replace(/^#/, '')}`)
  if (ref.startsWith('log trace_id=')) return found('log', `trace ${ref.slice(13).trim()}`)
  if (ref.startsWith('log:')) return found('log', `trace ${ref.slice(4).trim()}`)
  if (ref.startsWith('log deploy at ')) return found('log', `deploy ${deployTime(ref.slice(14))}`)
  if (ref.startsWith('memory:')) return found('memory', 'memory')
  if (INCIDENT_KEY.test(ref)) return found('incident', ref.toUpperCase())
  if (/^[a-z_]+:/.test(ref)) return { kind: null, short: ref, opens: false }

  match = CITATION.exec(ref)
  if (!match) return { kind: null, short: ref, opens: false }
  const [, path, section, symbol, start, end] = match
  const name = fileName(path)
  if (section) return found(DOC_SUFFIX.test(path) ? 'doc' : 'code', `${name} § ${section.split(' > ').at(-1)}`)
  if (symbol) return found('code', `${name} :: ${symbol}${lines(start, end)}`)
  return found(DOC_SUFFIX.test(path) ? 'doc' : 'code', `${name}${lines(start, end)}`)
}

function found(kind: SourceKind, short: string): RefSummary {
  return { kind, short, opens: true }
}

/** "2026-10-06T14:20:00+00:00" → "14:20 UTC"; anything else as it is. */
function deployTime(text: string): string {
  const time = new Date(text.trim())
  return Number.isNaN(time.getTime()) ? text.trim() : `${time.toISOString().slice(11, 16)} UTC`
}

/** GitHub's anchor for a heading, as the backend gives a doc's `section`: lower case,
 * punctuation dropped, spaces to dashes. "Pool sizing" → "pool-sizing". */
export function headingSlug(heading: string): string {
  return heading
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_\- ]/gu, '')
    .replace(/ /g, '-')
}
