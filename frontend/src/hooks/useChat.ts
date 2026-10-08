import { useCallback, useReducer } from 'react'
import { ApiError, chatEventsUrl } from '@/api/client'
import { useDeps } from '@/api/context'
import type { ChatView, Message } from '@/api/types'
import { useEventSource } from './useEventSource'

export interface ChatState {
  view: ChatView | null
  optimistic: Message | null // the customer's message, shown before the server echoes it
  submitting: boolean
  reset: boolean
  notFound: boolean
  busy: boolean // the app was full and refused the last message
}

type Action =
  | { type: 'state'; view: ChatView }
  | { type: 'submit'; optimistic?: Message }
  | { type: 'failed' }
  | { type: 'reset' }
  | { type: 'not_found' }
  | { type: 'busy' }

export const initialChatState: ChatState = {
  view: null,
  optimistic: null,
  submitting: false,
  reset: false,
  notFound: false,
  busy: false,
}

export function chatReducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'state':
      return {
        ...state,
        view: action.view,
        // Once the server has processed the turn, its copy of the message is in the view.
        optimistic: action.view.running ? state.optimistic : null,
        submitting: action.view.running ? state.submitting : false,
      }
    case 'submit':
      return {
        ...state,
        submitting: true,
        busy: false,
        optimistic: action.optimistic ?? state.optimistic,
      }
    case 'failed':
      return { ...state, submitting: false, optimistic: null }
    case 'reset':
      return { ...state, reset: true }
    case 'not_found':
      return { ...state, notFound: true }
    case 'busy':
      return { ...state, busy: true }
  }
}

export function useChat(chatId: string | null) {
  const { api } = useDeps()
  const [state, dispatch] = useReducer(chatReducer, initialChatState)

  const status = useEventSource(chatId ? chatEventsUrl(chatId) : null, {
    state: (data) => dispatch({ type: 'state', view: data as ChatView }),
    reset: () => dispatch({ type: 'reset' }),
  })

  const handleError = useCallback(
    async (err: unknown) => {
      dispatch({ type: 'failed' })
      if (err instanceof ApiError && err.status === 404) dispatch({ type: 'not_found' })
      else if (err instanceof ApiError && err.code === 'at_capacity') dispatch({ type: 'busy' })
      else if (chatId) {
        // e.g. 409: someone else answered first. Show the server's truth.
        try {
          dispatch({ type: 'state', view: await api.getChat(chatId) })
        } catch {
          /* the stream will catch up */
        }
      }
      return err
    },
    [api, chatId],
  )

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const pending = state.view?.pending
      if (!chatId || !pending || pending.type !== 'await_customer') return false
      const optimistic: Message = {
        id: `local-${Date.now()}`,
        role: 'customer',
        text,
        kind: 'text',
      }
      dispatch({ type: 'submit', optimistic })
      try {
        await api.send(chatId, pending.interrupt_id, text)
        return true
      } catch (err) {
        await handleError(err)
        return false
      }
    },
    [api, chatId, state.view, handleError],
  )

  const answer = useCallback(
    async (confirmed: boolean) => {
      const pending = state.view?.pending
      if (!chatId || !pending || pending.type !== 'confirm') return
      dispatch({ type: 'submit' })
      try {
        await api.confirm(chatId, pending.interrupt_id, confirmed)
      } catch (err) {
        await handleError(err)
      }
    },
    [api, chatId, state.view, handleError],
  )

  const retry = useCallback(async () => {
    if (!chatId) return
    dispatch({ type: 'submit' })
    try {
      await api.retry(chatId)
    } catch (err) {
      await handleError(err)
    }
  }, [api, chatId, handleError])

  return { state, status, send, answer, retry }
}
