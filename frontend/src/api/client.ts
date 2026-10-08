import type { Approval, ChatView, Meta } from './types'

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => null)
    const err = data?.error ?? { code: 'http_error', message: res.statusText }
    throw new ApiError(res.status, err.code, err.message)
  }
  return (await res.json()) as T
}

export const api = {
  meta: () => request<Meta>('GET', '/meta'),
  createChat: () => request<ChatView>('POST', '/chats'),
  getChat: (id: string) => request<ChatView>('GET', `/chats/${id}`),
  send: (id: string, interrupt_id: string, text: string) =>
    request<ChatView>('POST', `/chats/${id}/messages`, { interrupt_id, text }),
  confirm: (id: string, interrupt_id: string, confirmed: boolean) =>
    request<ChatView>('POST', `/chats/${id}/confirm`, { interrupt_id, confirmed }),
  retry: (id: string) => request<ChatView>('POST', `/chats/${id}/retry`),
  approvals: () => request<{ approvals: Approval[] }>('GET', '/supervisor/approvals'),
  decide: (chatId: string, interrupt_id: string, approved: boolean, note: string | null) =>
    request<ChatView>('POST', `/supervisor/approvals/${chatId}/decision`, {
      interrupt_id,
      approved,
      note,
    }),
  reset: () => request<{ chats_deleted: number }>('POST', '/admin/reset'),
}

export type Api = typeof api

export const chatEventsUrl = (id: string) => `/api/chats/${id}/events`
export const supervisorEventsUrl = '/api/supervisor/events'
