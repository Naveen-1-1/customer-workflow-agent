// Mirrors src/customer_workflow_agent/api/schemas.py

export type Role = 'agent' | 'customer' | 'event'

export interface Message {
  id: string
  role: Role
  text: string
  kind: string
}

export interface ConfirmSummary {
  title: string
  lines: string[]
  amount: number | null
  amount_label: string | null
}

export interface Pending {
  type: 'await_customer' | 'confirm' | 'supervisor_approval'
  interrupt_id: string
  action?: string | null
  summary?: ConfirmSummary | null
  refund_total?: number | null
}

export interface ChatView {
  chat_id: string
  created_at: string
  messages: Message[]
  pending: Pending | null
  running: boolean
  ended: boolean
  end_reason: 'goodbye' | 'transferred' | null
  error: string | null
}

export interface ApprovalItem {
  name: string
  options: Record<string, string>
  price: number
}

export interface ApprovalRequest {
  order_id: string
  user_id: string
  items: ApprovalItem[]
  refund_total: number
  payment_method_id: string
  payment_method: string
}

export interface Approval {
  chat_id: string
  interrupt_id: string
  status: 'pending' | 'processing'
  requested_at: string
  request: ApprovalRequest
}

export interface Meta {
  approval_enabled: boolean
  approval_threshold: number
  llm_configured: boolean
  primary_model: string
  fallback_model: string
}
