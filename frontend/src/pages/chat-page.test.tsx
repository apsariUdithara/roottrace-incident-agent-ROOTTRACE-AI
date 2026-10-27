// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSession } from '@/api/session'
import type { Schemas } from '@/api/types'
import { historyForRequest, type ChatTurn } from '@/lib/chat'
import { queryClient } from '@/lib/query-client'
import { backend, json, renderApp, stubMatchMedia } from '@/test/helpers'

type ChatRequest = Schemas['ChatRequest']

function answer(content: string, tier: 'nano' | 'super' = 'nano'): Schemas['ChatResponse'] {
  return {
    content,
    model: tier === 'nano' ? 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B' : 'nvidia/nemotron-3-super-120b-a12b',
    tier,
    prompt_tokens: 40,
    completion_tokens: 25,
    cost_usd: 0.00042,
    latency_ms: 1234,
    cached: false,
  }
}

/** Opens the chat page with a backend whose POST /chat answers with `reply`; returns the
 * requests it received. */
async function openChat(reply: (request: ChatRequest) => Response) {
  const requests: ChatRequest[] = []
  backend({
    cookie: true,
    chat: (request) => {
      requests.push(request)
      return reply(request)
    },
  })
  renderApp('/chat')
  await screen.findByRole('heading', { name: 'Chat' })
  return requests
}

const conversation = () => within(screen.getByRole('log', { name: 'Conversation' }))

beforeEach(() => stubMatchMedia())

afterEach(() => {
  cleanup()
  clearSession()
  queryClient.clear()
  vi.unstubAllGlobals()
})

describe('chat', () => {
  it('asks Nemotron and shows the answer as Markdown, with the model that answered', async () => {
    const requests = await openChat(() =>
      json(200, answer('**Pool exhaustion**: 5 instances × 100 > `max_connections`.')),
    )

    await userEvent.setup().type(screen.getByLabelText('Message'), 'Why too many connections?{Enter}')

    expect(await conversation().findByText('Pool exhaustion')).toHaveProperty('tagName', 'STRONG')
    expect(conversation().getByText('Why too many connections?')).toBeTruthy()
    expect(
      conversation().getByText(
        'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B · 1.2 s · 65 tokens · $0.0004',
      ),
    ).toBeTruthy()
    expect(requests).toEqual([
      { messages: [{ role: 'user', content: 'Why too many connections?' }], tier: 'nano' },
    ])
    expect(screen.getByLabelText('Message')).toHaveProperty('value', '') // ready for the next
  })

  it('sends the conversation so far, with the chosen model', async () => {
    const requests = await openChat((request) =>
      json(200, answer(`Answer ${request.messages.length}`, request.tier)),
    )
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message'), 'First question{Enter}')
    await conversation().findByText('Answer 1')
    await user.selectOptions(screen.getByLabelText('Model'), 'super')
    await user.type(screen.getByLabelText('Message'), 'Follow-up{Enter}')
    await conversation().findByText('Answer 3')

    expect(requests[1]).toEqual({
      messages: [
        { role: 'user', content: 'First question' },
        { role: 'assistant', content: 'Answer 1' },
        { role: 'user', content: 'Follow-up' },
      ],
      tier: 'super',
    })
  })

  it('starts a new line with Shift+Enter instead of sending', async () => {
    const requests = await openChat(() => json(200, answer('unused')))
    const box = screen.getByLabelText('Message')

    await userEvent.setup().type(box, 'line one{Shift>}{Enter}{/Shift}line two')

    expect(box).toHaveProperty('value', 'line one\nline two')
    expect(requests).toEqual([])
  })

  it("shows the backend's reason for refusing, and Try again sends it again", async () => {
    let refuse = true
    const requests = await openChat(() =>
      refuse
        ? json(429, { detail: 'Limit reached: 30 chat messages per hour.', code: 'too_many_requests' })
        : json(200, answer('Here you go.')),
    )
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('Message'), 'Hello there{Enter}')
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Limit reached: 30 chat messages per hour.',
    )

    refuse = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await conversation().findByText('Here you go.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(requests).toHaveLength(2)
    expect(requests[1].messages).toEqual([{ role: 'user', content: 'Hello there' }])
  })

  it("never renders HTML from the model's answer", async () => {
    await openChat(() => json(200, answer('<img src="x" onerror="alert(1)"> Done.')))

    await userEvent.setup().type(screen.getByLabelText('Message'), 'Show me HTML{Enter}')

    await conversation().findByText(/Done\./)
    expect(screen.getByRole('log').querySelector('img')).toBeNull()
  })

  it('offers example questions on an empty chat', async () => {
    const requests = await openChat(() => json(200, answer('An example answer.')))
    const example = screen.getByRole('button', { name: /too many connections/ })

    await userEvent.setup().click(example)

    expect(await conversation().findByText('An example answer.')).toBeTruthy()
    expect(requests[0].messages[0].content).toBe(example.textContent)
    expect(screen.queryByRole('button', { name: /too many connections/ })).toBeNull()
  })
})

describe('historyForRequest', () => {
  const turn = (i: number): ChatTurn =>
    i % 2 === 0
      ? { role: 'user', content: `q${i}` }
      : { role: 'assistant', content: `a${i}`, answer: answer(`a${i}`) }

  it('keeps a short conversation as it is', () => {
    expect(historyForRequest([turn(0), turn(1), turn(2)])).toEqual([
      { role: 'user', content: 'q0' },
      { role: 'assistant', content: 'a1' },
      { role: 'user', content: 'q2' },
    ])
  })

  it('sends at most the latest 20 messages, starting with a question', () => {
    const history = historyForRequest(Array.from({ length: 26 }, (_, i) => turn(i)))

    expect(history.length).toBeLessThanOrEqual(20)
    expect(history[0]).toEqual({ role: 'user', content: 'q6' })
    expect(history.at(-1)).toEqual({ role: 'assistant', content: 'a25' })
  })
})
