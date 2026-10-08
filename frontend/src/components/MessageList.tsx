import { useEffect, useRef } from 'react'
import type { Message } from '@/api/types'
import { cn } from '@/lib/utils'

export function MessageList({ messages, typing }: { messages: Message[]; typing: boolean }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, typing])

  return (
    <div className="flex flex-col gap-2" aria-live="polite">
      {messages.map((m) =>
        m.role === 'event' ? (
          <p key={m.id} className="self-center text-xs text-muted-foreground">
            {m.text}
          </p>
        ) : (
          <div
            key={m.id}
            data-role={m.role}
            className={cn(
              'max-w-[80%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap',
              m.role === 'customer'
                ? 'self-end bg-primary text-primary-foreground'
                : 'self-start bg-muted',
              m.kind === 'transfer' && 'font-semibold',
            )}
          >
            {m.text}
          </div>
        ),
      )}
      {typing && (
        <div className="self-start rounded-2xl bg-muted px-3 py-2 text-sm text-muted-foreground">
          <span className="animate-pulse">Typing…</span>
        </div>
      )}
      <div ref={end} />
    </div>
  )
}
