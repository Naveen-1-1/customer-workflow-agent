import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface Props {
  disabled: boolean
  placeholder?: string
  onSend: (text: string) => Promise<boolean>
}

export function Composer({ disabled, placeholder, onSend }: Props) {
  const [text, setText] = useState('')

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
    <form onSubmit={submit} className="flex items-end gap-2">
      <Textarea
        aria-label="Message"
        value={text}
        maxLength={2000}
        rows={2}
        disabled={disabled}
        placeholder={placeholder ?? 'Type a message…'}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        className="min-h-12 resize-none"
      />
      <Button type="submit" disabled={disabled || !text.trim()}>
        Send
      </Button>
    </form>
  )
}
