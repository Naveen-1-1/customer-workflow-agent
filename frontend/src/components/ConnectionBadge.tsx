import { Badge } from '@/components/ui/badge'
import type { StreamStatus } from '@/hooks/useEventSource'

const LABEL: Record<StreamStatus, string> = {
  open: 'Live',
  connecting: 'Connecting…',
  closed: 'Offline',
}

export function ConnectionBadge({ status }: { status: StreamStatus }) {
  return (
    <Badge variant={status === 'open' ? 'secondary' : 'outline'} title="Live updates">
      <span
        className={`mr-1 inline-block size-2 rounded-full ${
          status === 'open' ? 'bg-green-500' : status === 'closed' ? 'bg-red-500' : 'bg-amber-500'
        }`}
      />
      {LABEL[status]}
    </Badge>
  )
}
