import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'
import { getSession, subscribeSession } from '@/api/session'

/** Retry a failed query once, unless the server said no: asking again won't change a 4xx. */
export function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status < 500) return false
  return failureCount < 1
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetry,
    },
  },
})

// When a session ends, drop everything cached for that user: someone else may log in next.
subscribeSession(() => {
  if (getSession() === null) queryClient.clear()
})
