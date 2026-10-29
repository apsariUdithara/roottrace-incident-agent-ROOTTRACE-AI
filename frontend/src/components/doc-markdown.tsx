import { useEffect, useRef } from 'react'

import { Markdown } from '@/components/markdown'
import { headingSlug } from '@/lib/source-refs'

/** A document (or an issue's body) as Markdown, scrolled to `section`: the cited heading, by the
 * slug the backend gives it, which is marked. Loaded lazily: Markdown rendering is heavy. */
export function DocMarkdown({ markdown, section }: { markdown: string; section?: string | null }) {
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!section) return
    const headings = box.current?.querySelectorAll('h1, h2, h3, h4, h5, h6') ?? []
    const cited = [...headings].find((heading) => headingSlug(heading.textContent ?? '') === section)
    if (!cited) return
    cited.setAttribute('data-cited', 'true')
    cited.scrollIntoView?.({ block: 'start' })
  }, [markdown, section])

  return (
    <div ref={box}>
      <Markdown className="[&_[data-cited]]:text-primary">{markdown}</Markdown>
    </div>
  )
}
