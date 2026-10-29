// Pages whose filters live in the URL (?q=pool&offset=50), so a view can be shared or reloaded.
import { useCallback, useEffect, useRef, useState } from 'react'
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

/** Changes the URL's search params, without a history entry. React Router's functional
 * setSearchParams starts from the params of the last render, so two quick changes (a filter,
 * then a search box's debounced text) could undo each other. This chains them instead: each one
 * starts from the one before. */
export function useSearchParamsUpdate(): (changes: Changes) => void {
  const [params, setParams] = useSearchParams()
  const latest = useRef(params)
  const sent = useRef<string[]>([]) // ours, not rendered yet
  useEffect(() => {
    const index = sent.current.indexOf(params.toString())
    if (index === -1) latest.current = params // from elsewhere (a link, Back): start from it
    else sent.current = sent.current.slice(index + 1) // ours has landed; later ones may follow
  }, [params])
  return useCallback(
    (changes: Changes) => {
      latest.current = withChanges(changes)(latest.current)
      sent.current.push(latest.current.toString())
      setParams(latest.current, { replace: true })
    },
    [setParams],
  )
}

/** A text box for the `name` param. What's typed goes into the URL once typing pauses, back on
 * the first page; a change to the URL from elsewhere (clearing the filters) shows in the box. */
export function useDebouncedParam(name: string): [string, (value: string) => void] {
  const [params] = useSearchParams()
  const update = useSearchParamsUpdate()
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
    const timer = setTimeout(() => update({ [name]: value, offset: null }), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [draft, applied, name, update])

  return [draft, setDraft]
}
