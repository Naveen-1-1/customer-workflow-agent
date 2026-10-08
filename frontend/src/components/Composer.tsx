import type { FormEvent, KeyboardEvent } from 'react'
import { ArrowUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface Props {
  // The draft lives in the page, so it survives the switch from the landing screen to the chat.
  text: string
  onTextChange: (text: string) => void
  disabled: boolean
  placeholder?: string
  autoFocus?: boolean
  onSend: (text: string) => Promise<boolean>
}

export function Composer({
  text,
  onTextChange: setText,
  disabled,
  placeholder,
  autoFocus,
  onSend,
}: Props) {
  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    const value = text.trim()
    if (!value || disabled) return
    setText('')
    if (!(await onSend(value))) setText(value) // put it back if it didn't go through
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-shadow focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30"
    >
      <Textarea
        aria-label="Message"
        value={text}
        maxLength={2000}
        rows={2}
        disabled={disabled}
        autoFocus={autoFocus}
        placeholder={placeholder ?? 'Type a message…'}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        className="min-h-12 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 disabled:bg-transparent dark:bg-transparent dark:disabled:bg-transparent"
      />
      <Button
        type="submit"
        size="icon"
        aria-label="Send"
        className="shrink-0 rounded-full"
        disabled={disabled || !text.trim()}
      >
        <ArrowUp />
      </Button>
    </form>
  )
}
