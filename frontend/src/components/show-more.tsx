import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'

/** The first `limit` items, and a button for the rest: long lists stay short until asked. */
export function useShowMore<T>(items: T[], limit: number): { shown: T[]; toggle: ReactNode } {
  const [all, setAll] = useState(false)
  if (items.length <= limit) return { shown: items, toggle: null }
  return {
    shown: all ? items : items.slice(0, limit),
    toggle: (
      <Button variant="ghost" size="sm" className="w-fit text-primary" onClick={() => setAll(!all)}>
        {all ? <ChevronUp data-icon="inline-start" /> : <ChevronDown data-icon="inline-start" />}
        {all ? 'Show less' : `Show all ${items.length}`}
      </Button>
    ),
  }
}
