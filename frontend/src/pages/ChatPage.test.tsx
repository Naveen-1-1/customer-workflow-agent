import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { chatReducer, initialChatState } from '@/hooks/useChat'
import { FakeEventSource } from '@/test/FakeEventSource'
import { confirmPending, fakeApi, renderAt, view } from '@/test/fixtures'
import { BUSY_TEXT, ChatPage } from './ChatPage'

function setup() {
  const api = fakeApi()
  renderAt('/?chat=c1', <ChatPage />, api)
  const es = FakeEventSource.last()
  const emit = (v: ReturnType<typeof view>) => act(() => es.emit('state', v))
  return { api, es, emit }
}

const composer = () => screen.getByRole('textbox', { name: 'Message' })

describe('ChatPage', () => {
  it('subscribes to the chat and sends with the pending interrupt id', async () => {
    const { api, es, emit } = setup()
    expect(es.url).toBe('/api/chats/c1/events')
    emit(view())
    expect(screen.getByText('Hi! How can I help?')).toBeInTheDocument()
    await userEvent.type(composer(), 'cancel my order{Enter}')
    expect(api.send).toHaveBeenCalledWith('c1', 'i1', 'cancel my order')
    expect(screen.getByText('cancel my order')).toBeInTheDocument() // shown right away
    expect(composer()).toBeDisabled()
    expect(screen.getByText('Typing…')).toBeInTheDocument()
  })

  it('opens the popup for a confirmation and answers it', async () => {
    const { api, emit } = setup()
    emit(view({ pending: confirmPending }))
    // The popup is modal: the rest of the page is hidden from assistive tech and disabled.
    expect(screen.getByRole('textbox', { name: 'Message', hidden: true })).toBeDisabled()
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, proceed' }))
    expect(api.confirm).toHaveBeenCalledWith('c1', 'i2', true)
  })

  it('shows the approval wait and keeps input disabled', () => {
    const { emit } = setup()
    emit(view({ pending: { type: 'supervisor_approval', interrupt_id: 'i3', refund_total: 1201.55 } }))
    expect(screen.getByText(/return of \$1,201\.55 needs a supervisor/)).toBeInTheDocument()
    expect(composer()).toBeDisabled()
  })

  it('offers a retry after a failed run', async () => {
    const { api, emit } = setup()
    emit(view({ pending: null, error: 'Something went wrong: boom' }))
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(api.retry).toHaveBeenCalledWith('c1')
  })

  it('hides the composer once the chat ends', () => {
    const { emit } = setup()
    emit(view({ pending: null, ended: true, end_reason: 'transferred' }))
    expect(screen.getByText('You have been transferred to a human agent.')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Message' })).not.toBeInTheDocument()
  })

  it('tells the customer when the demo was reset', () => {
    const { es, emit } = setup()
    emit(view())
    act(() => es.emit('reset', {}))
    expect(screen.getByText('The demo was reset.')).toBeInTheDocument()
  })

  it('shows the busy message when the app was full, with a way to try again', () => {
    renderAt('/?busy=1', <ChatPage />, fakeApi())
    expect(screen.getByText(BUSY_TEXT)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try again' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('textbox', { name: 'Message' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Connecting/)).not.toBeInTheDocument() // no chat to connect to
  })

  it('keeps the message and says busy when a message is refused for capacity', async () => {
    const { api, emit } = setup()
    api.send = vi.fn(async () => {
      throw new ApiError(503, 'at_capacity', BUSY_TEXT)
    })
    emit(view())
    await userEvent.type(composer(), 'cancel my order{Enter}')
    expect(await screen.findByText(BUSY_TEXT)).toBeInTheDocument()
    expect(composer()).toHaveValue('cancel my order')
    expect(composer()).toBeEnabled()
  })

  it('warns when the NVIDIA key is missing', async () => {
    const api = fakeApi()
    api.meta = async () => ({
      approval_enabled: false,
      approval_threshold: 1000,
      llm_configured: false,
      primary_model: 'p',
      fallback_model: 'f',
    })
    renderAt('/?chat=c1', <ChatPage />, api)
    await waitFor(() => expect(screen.getByText(/NVIDIA_API_KEY isn't set/)).toBeInTheDocument())
  })
})

describe('chatReducer', () => {
  it('keeps the optimistic message until the server finishes the turn', () => {
    const optimistic = { id: 'local', role: 'customer' as const, text: 'hi', kind: 'text' }
    let s = chatReducer(initialChatState, { type: 'submit', optimistic })
    s = chatReducer(s, { type: 'state', view: view({ running: true, pending: null }) })
    expect(s.optimistic).toEqual(optimistic)
    expect(s.submitting).toBe(true)
    s = chatReducer(s, { type: 'state', view: view() })
    expect(s.optimistic).toBeNull()
    expect(s.submitting).toBe(false)
  })
})
