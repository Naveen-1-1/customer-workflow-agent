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
          <p key={m.id} className="text-muted-foreground self-center text-xs">
            {m.text}
          </p>
        ) : (
          <div
            key={m.id}
            data-role={m.role}
            className={cn(
              'max-w-[80%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap',
              m.role === 'customer'
                ? 'bg-primary text-primary-foreground self-end'
                : 'bg-muted self-start',
              m.kind === 'transfer' && 'font-semibold',
            )}
          >
            {m.text}
          </div>
        ),
      )}
      {typing && (
        <div className="bg-muted text-muted-foreground self-start rounded-2xl px-3 py-2 text-sm">
          <span className="animate-pulse">Typing…</span>
        </div>
      )}
      <div ref={end} />
    </div>
  )
}
