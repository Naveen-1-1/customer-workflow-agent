import { useCallback, useState } from 'react'
import { supervisorEventsUrl } from '@/api/client'
import { useDeps } from '@/api/context'
import type { Approval } from '@/api/types'
import { useEventSource } from './useEventSource'

export function useApprovals() {
  const { api } = useDeps()
  const [approvals, setApprovals] = useState<Approval[] | null>(null)
  const [resetAt, setResetAt] = useState<number | null>(null)

  const status = useEventSource(supervisorEventsUrl, {
    approvals: (data) => setApprovals((data as { approvals: Approval[] }).approvals),
    reset: () => {
      setApprovals([])
      setResetAt(Date.now())
    },
  })

  const decide = useCallback(
    (a: Approval, approved: boolean, note: string | null) =>
      api.decide(a.chat_id, a.interrupt_id, approved, note),
    [api],
  )

  return { approvals, status, decide, resetAt }
}
