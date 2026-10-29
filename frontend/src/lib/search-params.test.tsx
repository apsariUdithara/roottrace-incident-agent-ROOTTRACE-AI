// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import { useSearchParamsUpdate, withChanges } from '@/lib/search-params'

function Filters() {
  const update = useSearchParamsUpdate()
  return (
    <button
      type="button"
      onClick={() => {
        // Two changes before the page renders again, like a filter and a debounced search.
        update({ level: 'ERROR', offset: null })
        update({ q: 'pool' })
      }}
    >
      Filter
    </button>
  )
}

function mount(path: string) {
  const router = createMemoryRouter([{ path: '/', element: <Filters /> }], { initialEntries: [path] })
  render(<RouterProvider router={router} />)
  return router
}

const search = (router: ReturnType<typeof mount>) =>
  Object.fromEntries(new URLSearchParams(router.state.location.search))

afterEach(cleanup)

describe('useSearchParamsUpdate', () => {
  it("chains quick changes, so the second doesn't undo the first", async () => {
    const router = mount('/?offset=50&route=/payments')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Filter' }))

    expect(search(router)).toEqual({ route: '/payments', level: 'ERROR', q: 'pool' })
    expect(router.state.historyAction).toBe('REPLACE') // no history entry per filter
  })

  it('starts from a change made elsewhere, such as a link', async () => {
    const router = mount('/?level=WARN')
    await act(() => router.navigate('/?trace_id=t-1'))

    await userEvent.setup().click(screen.getByRole('button', { name: 'Filter' }))

    expect(search(router)).toEqual({ trace_id: 't-1', level: 'ERROR', q: 'pool' })
  })
})

describe('withChanges', () => {
  it('sets values, and removes empty ones and offset 0', () => {
    const next = withChanges({ q: 'pool', level: '', offset: 0, service: null })(
      new URLSearchParams('level=ERROR&offset=50&service=api'),
    )
    expect(next.toString()).toBe('q=pool')
  })
})
