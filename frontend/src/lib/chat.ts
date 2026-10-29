import type { Schemas } from '@/api/types'

export type ChatTurn =
  | { role: 'user'; content: string }
  // projectId: the project the answer searched, where its sources open.
  | { role: 'assistant'; content: string; answer: Schemas['ChatResponse']; projectId?: string }

export const MAX_MESSAGES = 20 // what POST /chat accepts

/** The conversation as POST /chat takes it: the latest MAX_MESSAGES turns, starting with a
 * question (an answer without its question would only confuse the model). */
export function historyForRequest(turns: ChatTurn[]): Schemas['ChatMessage'][] {
  const recent = turns.slice(-MAX_MESSAGES)
  const start = recent.findIndex((turn) => turn.role === 'user')
  return recent.slice(Math.max(start, 0)).map(({ role, content }) => ({ role, content }))
}
