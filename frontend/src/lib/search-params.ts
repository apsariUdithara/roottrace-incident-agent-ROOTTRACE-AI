// Pages whose filters live in the URL (?q=pool&offset=50), so a view can be shared or reloaded.
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'

export const SEARCH_DELAY_MS = 300

export type Changes = Record<string, string | number | null>

/** The search params with `changes` applied; an empty value (or offset 0) removes the param. */
export function withChanges(changes: Changes) {
  return (current: URLSearchParams) => {
    const next = new URLSearchParams(current)
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '' || value === 0) next.delete(key)
      else next.set(key, String(value))
    }
    return next
  }
}

/** A text box for the `name` param. What's typed goes into the URL once typing pauses, back on
 * the first page; a change to the URL from elsewhere (clearing the filters) shows in the box. */
export function useDebouncedParam(name: string): [string, (value: string) => void] {
  const [params, setParams] = useSearchParams()
  const applied = params.get(name) ?? ''
  const [draft, setDraft] = useState(applied)
  const [seen, setSeen] = useState(applied)

  if (applied !== seen) {
    setSeen(applied)
    if (applied !== draft.trim()) setDraft(applied)
  }

  useEffect(() => {
    const value = draft.trim()
    if (value === applied) return
    const timer = setTimeout(
      () => setParams(withChanges({ [name]: value, offset: null }), { replace: true }),
      SEARCH_DELAY_MS,
    )
    return () => clearTimeout(timer)
  }, [draft, applied, name, setParams])

  return [draft, setDraft]
}
