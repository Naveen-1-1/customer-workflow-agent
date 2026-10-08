import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { ApiError } from '@/api/client'
import { useDeps } from '@/api/context'
import type { Approval, ChatView, Meta } from '@/api/types'
import { ConnectionBadge } from '@/components/ConnectionBadge'
import { MessageList } from '@/components/MessageList'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { useApprovals } from '@/hooks/useApprovals'
import { since, usd } from '@/lib/format'

type Decision = { approval: Approval; approved: boolean }

function DecisionDialog({
  decision,
  onClose,
  onSubmit,
}: {
  decision: Decision | null
  onClose: () => void
  onSubmit: (note: string | null) => Promise<void>
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  if (!decision) return null
  const { approval, approved } = decision
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {approved ? 'Approve' : 'Reject'} return of {usd(approval.request.refund_total)}?
          </DialogTitle>
          <DialogDescription>
            Order {approval.request.order_id} · {approval.request.user_id}
          </DialogDescription>
        </DialogHeader>
        <label className="grid gap-1 text-sm">
          <span>
            Note (optional){approved ? ', kept internal' : ', shown to the customer'}
          </span>
          <Textarea maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={approved ? 'default' : 'destructive'}
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onSubmit(note.trim() || null)
              } finally {
                setBusy(false)
              }
            }}
          >
            {approved ? 'Approve' : 'Reject'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TranscriptSheet({ chatId, onClose }: { chatId: string | null; onClose: () => void }) {
  const { api } = useDeps()
  const [loaded, setLoaded] = useState<{ id: string; view: ChatView } | null>(null)
  useEffect(() => {
    if (chatId) api.getChat(chatId).then((view) => setLoaded({ id: chatId, view }), () => {})
  }, [api, chatId])
  const view = loaded && loaded.id === chatId ? loaded.view : null
  return (
    <Sheet open={chatId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Conversation</SheetTitle>
          <SheetDescription>Read-only transcript of this chat so far.</SheetDescription>
        </SheetHeader>
        <div className="p-4">
          {view ? (
            <MessageList messages={view.messages} typing={false} />
          ) : (
            <p className="text-muted-foreground text-sm">Loading…</p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function ResetButton() {
  const { api } = useDeps()
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          Reset demo
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Reset the demo?</AlertDialogTitle>
          <AlertDialogDescription>
            This restores the store to the original τ²-bench data and deletes every chat, including
            ones in progress.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={async () => {
              try {
                const r = await api.reset()
                toast.success(`Demo reset (${r.chats_deleted} chats deleted)`)
              } catch {
                toast.error('Reset failed')
              }
            }}
          >
            Reset
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function SupervisorPage() {
  const { api } = useDeps()
  const { approvals, status, decide } = useApprovals()
  const [meta, setMeta] = useState<Meta | null>(null)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [transcript, setTranscript] = useState<string | null>(null)
  const [, setTick] = useState(0)

  useEffect(() => {
    api.meta().then(setMeta, () => setMeta(null))
  }, [api])
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 15000) // refresh "waiting since"
    return () => clearInterval(t)
  }, [])

  const submit = async (note: string | null) => {
    if (!decision) return
    try {
      await decide(decision.approval, decision.approved, note)
      setDecision(null)
    } catch (err) {
      setDecision(null)
      toast.error(err instanceof ApiError && err.status === 409 ? 'Already decided' : 'Failed')
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Supervisor · pending return approvals</h1>
        <div className="flex items-center gap-2">
          <ConnectionBadge status={status} />
          <ResetButton />
          <Button variant="ghost" size="sm" asChild>
            <Link to="/">Customer chat</Link>
          </Button>
        </div>
      </header>

      {meta && (
        <p className="text-muted-foreground text-sm">
          {meta.approval_enabled
            ? `Returns over ${usd(meta.approval_threshold)} wait here for your decision.`
            : 'Approval is switched off (APPROVAL_ENABLED=false), so returns go straight through.'}
        </p>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Waiting</TableHead>
            <TableHead>Customer</TableHead>
            <TableHead>Order</TableHead>
            <TableHead>Items</TableHead>
            <TableHead className="text-right">Refund</TableHead>
            <TableHead>Refund to</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {approvals?.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-muted-foreground text-center">
                No pending approvals
              </TableCell>
            </TableRow>
          )}
          {approvals?.map((a) => (
            <TableRow key={a.chat_id}>
              <TableCell>{since(a.requested_at)}</TableCell>
              <TableCell>{a.request.user_id}</TableCell>
              <TableCell>{a.request.order_id}</TableCell>
              <TableCell className="whitespace-normal">
                {a.request.items.map((i) => i.name).join(', ')}
              </TableCell>
              <TableCell className="text-right">{usd(a.request.refund_total)}</TableCell>
              <TableCell>{a.request.payment_method}</TableCell>
              <TableCell className="text-right">
                {a.status === 'processing' ? (
                  <span className="text-muted-foreground text-sm">Processing…</span>
                ) : (
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setTranscript(a.chat_id)}>
                      View chat
                    </Button>
                    <Button size="sm" onClick={() => setDecision({ approval: a, approved: true })}>
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => setDecision({ approval: a, approved: false })}
                    >
                      Reject
                    </Button>
                  </div>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <DecisionDialog
        key={decision ? `${decision.approval.interrupt_id}:${decision.approved}` : 'none'}
        decision={decision}
        onClose={() => setDecision(null)}
        onSubmit={submit}
      />
      <TranscriptSheet chatId={transcript} onClose={() => setTranscript(null)} />
    </div>
  )
}
