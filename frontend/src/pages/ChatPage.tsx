import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useDeps } from '@/api/context'
import type { Meta } from '@/api/types'
import { Composer } from '@/components/Composer'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ConnectionBadge } from '@/components/ConnectionBadge'
import { Landing } from '@/components/Landing'
import { MessageList } from '@/components/MessageList'
import { Suggestions } from '@/components/Suggestions'
import { Button } from '@/components/ui/button'
import { useChat } from '@/hooks/useChat'
import { usd } from '@/lib/format'

export const LAST_CHAT_KEY = 'cwa:lastChatId'
export const BUSY_TEXT = "We're busy right now, please try again in a minute."

function Notice({
  children,
  tone = 'info',
}: {
  children: React.ReactNode
  tone?: 'info' | 'warn'
}) {
  return (
    <div
      role="status"
      className={
        tone === 'warn'
          ? 'rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900'
          : 'rounded-lg bg-muted px-3 py-2 text-sm'
      }
    >
      {children}
    </div>
  )
}

export function ChatPage() {
  const [params] = useSearchParams()
  const chatId = params.get('chat')
  const refused = !chatId && params.has('busy') // the app was full when this chat would start
  const { api } = useDeps()
  const { state, status, send, answer, retry } = useChat(chatId)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [draft, setDraft] = useState('')

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
  const suggestions = canType ? (pending.suggestions ?? []) : []

  const keyMissing = meta && !meta.llm_configured && (
    <Notice tone="warn">
      NVIDIA_API_KEY isn't set, so the agent can't understand messages yet. Add it to .env and
      restart the backend.
    </Notice>
  )

  const busyNotice = state.busy && <Notice tone="warn">{BUSY_TEXT}</Notice>

  // Until the customer's first message, the chat is a landing screen (the greeting is implied).
  // Anything else needing attention (an error, a popup, the end) shows in the chat itself.
  const started = messages.some((m) => m.role === 'customer')
  const quiet = !view?.error && !view?.ended && !state.reset && !state.notFound
  if (view && !started && quiet && (!pending || pending.type === 'await_customer')) {
    return (
      <Landing
        text={draft}
        onTextChange={setDraft}
        scenarios={pending?.suggestions ?? []}
        canType={canType}
        notice={
          <>
            {keyMissing}
            {busyNotice}
          </>
        }
        onSend={send}
      />
    )
  }

  return (
    <div className="mx-auto flex h-dvh max-w-2xl flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Customer support</h1>
        <div className="flex items-center gap-2">
          {!refused && <ConnectionBadge status={status} />}
          <Button variant="outline" size="sm" asChild>
            <Link to="/?new=1">New chat</Link>
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/supervisor">Supervisor</Link>
          </Button>
        </div>
      </header>

      {keyMissing}

      <main className="flex-1 overflow-y-auto rounded-xl border bg-card p-3">
        {refused ? (
          <Notice tone="warn">
            <div className="flex items-center justify-between gap-2">
              <span>{BUSY_TEXT}</span>
              <Button size="sm" asChild>
                <Link to="/">Try again</Link>
              </Button>
            </div>
          </Notice>
        ) : state.notFound ? (
          <Notice>This chat no longer exists.</Notice>
        ) : view ? (
          <MessageList messages={messages} typing={busy} />
        ) : (
          <p className="text-sm text-muted-foreground">Connecting…</p>
        )}
      </main>

      {pending?.type === 'supervisor_approval' && (
        <Notice>
          Your return of {usd(pending.refund_total ?? 0)} needs a supervisor's approval. This page
          will update as soon as they decide.
        </Notice>
      )}
      {busyNotice}
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

      {!refused && !view?.ended && !state.reset && !state.notFound && (
        <>
          <Suggestions
            suggestions={suggestions}
            disabled={!canType}
            onPick={(text) => void send(text)}
          />
          <Composer
            text={draft}
            onTextChange={setDraft}
            disabled={!canType}
            placeholder={
              pending?.type === 'supervisor_approval' ? 'Waiting for approval…' : undefined
            }
            onSend={send}
          />
        </>
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
