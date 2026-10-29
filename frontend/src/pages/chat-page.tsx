import { useMutation } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import { api, ApiError, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { Markdown } from '@/components/markdown'
import { PageHeader } from '@/components/page-header'
import { CitationChip } from '@/components/source-chip'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'
import { historyForRequest, type ChatTurn as Turn } from '@/lib/chat'
import { useCurrentProject } from '@/lib/current-project'
import { useSourceViewer } from '@/lib/source-viewer'

type ChatTier = Schemas['ChatRequest']['tier']
type Answer = Schemas['ChatResponse']

const EXAMPLES = [
  'What usually causes "too many connections" errors in PostgreSQL?',
  'How do I tell a bad deploy from a traffic spike in my logs?',
  'Explain idempotency keys for payment APIs in three sentences.',
]

export function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [tier, setTier] = useState<ChatTier>('nano')
  // On by default: answers from the project's own knowledge, with their sources.
  const [searchProject, setSearchProject] = useState(true)
  const { project } = useCurrentProject()
  const projectId = project && searchProject ? project.id : undefined
  const end = useRef<HTMLDivElement>(null)

  const send = useMutation({
    mutationFn: async (history: Turn[]) => {
      const searched = projectId // the answer's sources open in the project it searched
      const answer = await ok(
        api.POST('/chat', {
          body: { messages: historyForRequest(history), tier, project_id: searched },
        }),
      )
      return { answer, searched }
    },
    onSuccess: ({ answer, searched }) =>
      setTurns((current) => [
        ...current,
        { role: 'assistant', content: answer.content, answer, projectId: searched },
      ]),
  })

  useEffect(() => end.current?.scrollIntoView?.({ block: 'end' }), [turns, send.isPending])

  function ask(question: string) {
    const content = question.trim()
    if (!content || send.isPending) return
    const next: Turn[] = [...turns, { role: 'user', content }]
    setTurns(next)
    setDraft('')
    send.mutate(next)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    ask(draft)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault() // Enter sends; Shift+Enter starts a new line
      ask(draft)
    }
  }

  function startOver() {
    send.reset()
    setTurns([])
  }

  return (
    <div className="flex max-w-3xl flex-1 flex-col">
      <PageHeader
        title="Chat"
        description="Ask Nemotron on Nebius. With the project search on, it answers from the project's docs, code, issues and past incidents, and shows the sources it used."
      />

      <div role="log" aria-label="Conversation" className="flex flex-1 flex-col gap-4">
        {turns.length === 0 && (
          <div className="flex flex-col items-start gap-2">
            <p className="text-xs text-muted-foreground">Try one of these:</p>
            {EXAMPLES.map((example) => (
              <Button key={example} variant="outline" size="sm" onClick={() => ask(example)}>
                {example}
              </Button>
            ))}
          </div>
        )}
        {turns.map((turn, index) =>
          turn.role === 'user' ? (
            <div key={index} className="self-end rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap">
              {turn.content}
            </div>
          ) : (
            <div key={index} className="flex flex-col gap-1">
              <Markdown>{turn.content}</Markdown>
              <Sources citations={turn.answer.citations} projectId={turn.projectId} />
              <AnswerDetails answer={turn.answer} />
            </div>
          ),
        )}
        {send.isPending && <p className="text-sm text-muted-foreground">Thinking…</p>}
        {send.isError && (
          <div className="flex items-center gap-3">
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(send.error)}
            </p>
            <Button variant="outline" size="sm" onClick={() => send.mutate(turns)}>
              Try again
            </Button>
          </div>
        )}
        <div ref={end} />
      </div>

      <form onSubmit={handleSubmit} className="sticky bottom-0 mt-4 flex flex-col gap-2 bg-background py-3">
        <Textarea
          aria-label="Message"
          placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
          value={draft}
          maxLength={4000}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="flex items-center gap-2">
          <NativeSelect
            size="sm"
            aria-label="Model"
            value={tier}
            onChange={(event) => setTier(event.target.value as ChatTier)}
          >
            <NativeSelectOption value="nano">Nemotron Nano: fast</NativeSelectOption>
            <NativeSelectOption value="super">Nemotron Super: stronger</NativeSelectOption>
          </NativeSelect>
          {project && (
            <Label className="gap-1.5 font-normal">
              <input
                type="checkbox"
                checked={searchProject}
                onChange={(event) => setSearchProject(event.target.checked)}
                className="accent-primary"
              />
              Search {project.name}
            </Label>
          )}
          {turns.length > 0 && (
            <Button type="button" variant="ghost" size="sm" onClick={startOver}>
              New chat
            </Button>
          )}
          <Button type="submit" size="sm" className="ml-auto" disabled={!draft.trim() || send.isPending}>
            <Send data-icon="inline-start" />
            Send
          </Button>
        </div>
      </form>
    </div>
  )
}

/** The sources an answer cites, as chips; the answer refers to them by label, e.g. [S1]. Each
 * opens in the source viewer. */
function Sources({ citations, projectId }: { citations: Answer['citations']; projectId?: string }) {
  const openSource = useSourceViewer()
  if (citations.length === 0) return null
  return (
    <ul aria-label="Sources" className="flex flex-wrap gap-1.5">
      {citations.map((citation) => (
        <li key={citation.label} className="max-w-full">
          <CitationChip
            citation={citation}
            onOpen={projectId ? () => openSource(projectId, citation.ref) : undefined}
          />
        </li>
      ))}
    </ul>
  )
}

/** Which model answered, and what it took: the proof it ran on Nemotron. */
function AnswerDetails({ answer }: { answer: Answer }) {
  const tokens = answer.prompt_tokens + answer.completion_tokens
  const parts = [
    answer.model,
    `${(answer.latency_ms / 1000).toFixed(1)} s`,
    `${tokens} tokens`,
    answer.cached ? 'cached' : `$${answer.cost_usd.toFixed(4)}`,
  ]
  return <p className="text-xs text-muted-foreground">{parts.join(' · ')}</p>
}

function errorMessage(error: Error): string {
  if (error instanceof ApiError) return error.message // e.g. the hourly limit, or a provider outage
  return "Can't reach the server. Check your connection and try again."
}
