import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'
import type { Api } from '@/api/client'
import { DepsProvider } from '@/api/context'
import type { Approval, ChatView, Meta, Pending } from '@/api/types'
import { FakeEventSource } from './FakeEventSource'

export const meta: Meta = {
  approval_enabled: true,
  approval_threshold: 1000,
  llm_configured: true,
  primary_model: 'p',
  fallback_model: 'f',
}

export function view(overrides: Partial<ChatView> = {}): ChatView {
  return {
    chat_id: 'c1',
    created_at: '2026-10-06T00:00:00+00:00',
    messages: [{ id: 'm1', role: 'agent', text: 'Hi! How can I help?', kind: 'text' }],
    pending: { type: 'await_customer', interrupt_id: 'i1' },
    running: false,
    ended: false,
    end_reason: null,
    error: null,
    ...overrides,
  }
}

export const confirmPending: Pending = {
  type: 'confirm',
  interrupt_id: 'i2',
  action: 'cancel_order',
  summary: {
    title: 'Cancel order #W1234567',
    lines: ['Mug (color: white) — $10.00', 'Reason: no longer needed'],
    amount: 10,
    amount_label: 'Refund',
  },
}

export const approval: Approval = {
  chat_id: 'c1',
  interrupt_id: 'i9',
  status: 'pending',
  requested_at: new Date().toISOString(),
  request: {
    order_id: '#W1067251',
    user_id: 'raj_sanchez_2970',
    items: [{ name: 'Grill', options: {}, price: 942.71 }],
    refund_total: 1201.55,
    payment_method_id: 'credit_card_3362387',
    payment_method: 'Visa card ending 1234',
  },
}

export function fakeApi(): Api {
  return {
    meta: vi.fn(async () => meta),
    createChat: vi.fn(async () => view()),
    getChat: vi.fn(async () => view()),
    send: vi.fn(async () => view({ running: true, pending: null })),
    confirm: vi.fn(async () => view({ running: true, pending: null })),
    retry: vi.fn(async () => view({ running: true })),
    approvals: vi.fn(async () => ({ approvals: [] })),
    decide: vi.fn(async () => view()),
    reset: vi.fn(async () => ({ chats_deleted: 0 })),
  }
}

const openEvents = (url: string) => new FakeEventSource(url) as unknown as EventSource

export function renderAt(path: string, element: ReactNode, api: Api) {
  FakeEventSource.instances = []
  const router = createMemoryRouter([{ path: path.split('?')[0], element }], {
    initialEntries: [path],
  })
  return render(
    <DepsProvider value={{ api, openEvents }}>
      <RouterProvider router={router} />
    </DepsProvider>,
  )
}
