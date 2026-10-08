import type { Suggestion } from '@/api/types'
import { Button } from '@/components/ui/button'

const MAX = 3 // the backend sends at most 3; this keeps the row short regardless

interface Props {
  suggestions: Suggestion[]
  disabled: boolean
  onPick: (text: string) => void
}

/** Replies the workflow offers for its current question; a click sends one. */
export function Suggestions({ suggestions, disabled, onPick }: Props) {
  if (!suggestions.length) return null
  return (
    <div className="flex flex-wrap gap-2" aria-label="Suggested replies">
      {suggestions.slice(0, MAX).map((s) => (
        <Button
          key={s.label}
          variant="outline"
          size="sm"
          className="h-auto max-w-full rounded-full py-1 text-left whitespace-normal"
          disabled={disabled}
          onClick={() => onPick(s.text)}
        >
          {s.label}
        </Button>
      ))}
    </div>
  )
}
