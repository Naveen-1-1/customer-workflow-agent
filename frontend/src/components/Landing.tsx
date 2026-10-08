import { Sparkle } from 'lucide-react'
import { Link } from 'react-router'
import type { Suggestion } from '@/api/types'
import { Composer } from '@/components/Composer'

const REPO_URL = 'https://github.com/Naveen-1-1/customer-workflow-agent'

function greeting(hour: number = new Date().getHours()): string {
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

interface Props {
  text: string
  onTextChange: (text: string) => void
  scenarios: Suggestion[]
  canType: boolean
  notice?: React.ReactNode
  onSend: (text: string) => Promise<boolean>
}

/** The chat before the customer's first message: a welcome, the box, and demo scenarios. */
export function Landing({ text, onTextChange, scenarios, canType, notice, onSend }: Props) {
  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col px-4">
      <main className="flex flex-1 flex-col justify-center gap-8 py-10">
        <div className="text-center">
          <h1 className="flex items-center justify-center gap-3 font-display text-4xl tracking-tight sm:text-5xl">
            <Sparkle aria-hidden className="size-8 fill-amber-500 text-amber-500 sm:size-10" />
            {greeting()}
          </h1>
          <p className="mt-3 text-lg">How can I help with your order?</p>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            Cancel, change, return or exchange your orders. Nothing changes until you confirm.
          </p>
        </div>

        {notice}
        <Composer
          text={text}
          onTextChange={onTextChange}
          disabled={!canType}
          autoFocus
          onSend={onSend}
        />

        {scenarios.length > 0 && (
          <section aria-label="Try a demo customer" className="grid gap-2 sm:grid-cols-3">
            {scenarios.slice(0, 3).map((s) => (
              <button
                key={s.label}
                type="button"
                disabled={!canType}
                onClick={() => void onSend(s.text)}
                className="flex flex-col items-start gap-1 rounded-xl border bg-card p-3 text-left transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-50"
              >
                <span className="text-sm font-medium">{s.label}</span>
                <span className="line-clamp-2 text-xs text-muted-foreground">{s.text}</span>
              </button>
            ))}
          </section>
        )}
      </main>

      <footer className="flex justify-center gap-3 py-4 text-xs text-muted-foreground">
        <a className="hover:text-foreground" href={`${REPO_URL}#how-it-works`}>
          How it works
        </a>
        <span aria-hidden>·</span>
        <a className="hover:text-foreground" href={REPO_URL}>
          GitHub
        </a>
        <span aria-hidden>·</span>
        <Link className="hover:text-foreground" to="/supervisor">
          Supervisor view
        </Link>
      </footer>
    </div>
  )
}
