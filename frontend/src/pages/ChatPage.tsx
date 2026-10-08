import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useDeps } from '@/api/context'
import type { Meta } from '@/api/types'
import { Composer } from '@/components/Composer'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ConnectionBadge } from '@/components/ConnectionBadge'
import { MessageList } from '@/components/MessageList'
import { Button } from '@/components/ui/button'
import { useChat } from '@/hooks/useChat'
import { usd } from '@/lib/format'

export const LAST_CHAT_KEY = 'cwa:lastChatId'

function Notice({ children, tone = 'info' }: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <div
      role="status"
      className={
        tone === 'warn'
          ? 'rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900'
          : 'bg-muted rounded-lg px-3 py-2 text-sm'
      }
    >
      {children}
    </div>
  )
}

export function ChatPage() {
  const [params] = useSearchParams()
  const chatId = params.get('chat')
  const { api } = useDeps()
  const { state, status, send, answer, retry } = useChat(chatId)
  const [meta, setMeta] = useState<Meta | null>(null)

  useEffect(() => {
    api.meta().then(setMeta, () => setMeta(null))
  }, [api])
  useEffect(() => {
    if (chatId) {
      try {
        localStorage.setItem(LAST_CHAT_KEY, chatId)
      } catch {
        /* storage may be unavailable */
      }
    }
  }, [chatId])

  const { view, optimistic, submitting } = state
  const pending = view && !view.running ? view.pending : null
  const busy = submitting || Boolean(view?.running)
  const messages = view ? [...view.messages, ...(optimistic ? [optimistic] : [])] : []
  const canType = pending?.type === 'await_customer' && !busy && !state.reset

  return (
    <div className="mx-auto flex h-dvh max-w-2xl flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Customer support</h1>
        <div className="flex items-center gap-2">
          <ConnectionBadge status={status} />
          <Button variant="outline" size="sm" asChild>
            <Link to="/?new=1">New chat</Link>
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/supervisor">Supervisor</Link>
          </Button>
        </div>
      </header>

      {meta && !meta.llm_configured && (
        <Notice tone="warn">
          NVIDIA_API_KEY isn't set, so the agent can't understand messages yet. Add it to .env
          and restart the backend.
        </Notice>
      )}

      <main className="flex-1 overflow-y-auto rounded-xl border p-3">
        {state.notFound ? (
          <Notice>This chat no longer exists.</Notice>
        ) : view ? (
          <MessageList messages={messages} typing={busy} />
        ) : (
          <p className="text-muted-foreground text-sm">Connecting…</p>
        )}
      </main>

      {pending?.type === 'supervisor_approval' && (
        <Notice>
          Your return of {usd(pending.refund_total ?? 0)} needs a supervisor's approval. This page
          will update as soon as they decide.
        </Notice>
      )}
      {view?.error && !busy && (
        <Notice tone="warn">
          <div className="flex items-center justify-between gap-2">
            <span>{view.error}</span>
            <Button size="sm" onClick={() => void retry()}>
              Retry
            </Button>
          </div>
        </Notice>
      )}
      {(view?.ended || state.reset || state.notFound) && (
        <Notice>
          <div className="flex items-center justify-between gap-2">
            <span>
              {state.reset
                ? 'The demo was reset.'
                : view?.end_reason === 'transferred'
                  ? 'You have been transferred to a human agent.'
                  : 'This chat has ended.'}
            </span>
            <Button size="sm" asChild>
              <Link to="/?new=1">Start a new chat</Link>
            </Button>
          </div>
        </Notice>
      )}

      {!view?.ended && !state.reset && !state.notFound && (
        <Composer
          disabled={!canType}
          placeholder={pending?.type === 'supervisor_approval' ? 'Waiting for approval…' : undefined}
          onSend={send}
        />
      )}

      {pending?.type === 'confirm' && pending.summary && (
        <ConfirmDialog
          key={pending.interrupt_id}
          open
          summary={pending.summary}
          action={pending.action}
          onAnswer={(yes) => void answer(yes)}
        />
      )}
    </div>
  )
}
