import { useState } from 'react'
import type { ConfirmSummary } from '@/api/types'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { usd } from '@/lib/format'

interface Props {
  open: boolean
  summary: ConfirmSummary
  action: string | null | undefined
  onAnswer: (confirmed: boolean) => void
}

// The explicit "yes" before any change. It can't be dismissed (no Esc, no click outside):
// the customer must choose. Render with key={interrupt_id} so each question starts fresh.
export function ConfirmDialog({ open, summary, action, onAnswer }: Props) {
  const [answered, setAnswered] = useState(false)
  const transfer = action === 'transfer'
  const choose = (yes: boolean) => {
    if (answered) return
    setAnswered(true)
    onAnswer(yes)
  }

  return (
    <AlertDialog open={open} onOpenChange={() => {}}>
      <AlertDialogContent
        className="sm:max-w-md data-[size=default]:sm:max-w-md"
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <AlertDialogHeader className="sm:place-items-start sm:text-left">
          <AlertDialogTitle>{summary.title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <ul className="space-y-1 text-left text-sm">
              {summary.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {summary.amount != null && (
          <p className="text-sm font-medium">
            {summary.amount_label ?? 'Amount'}: {usd(summary.amount)}
          </p>
        )}
        <AlertDialogFooter>
          <Button variant="outline" disabled={answered} onClick={() => choose(false)}>
            {transfer ? 'No, stay here' : 'No, go back'}
          </Button>
          <Button disabled={answered} onClick={() => choose(true)}>
            {transfer ? 'Yes, transfer me' : 'Yes, proceed'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
