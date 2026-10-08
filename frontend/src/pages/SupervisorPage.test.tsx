import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { FakeEventSource } from '@/test/FakeEventSource'
import { approval, fakeApi, renderAt, view } from '@/test/fixtures'
import { SupervisorPage } from './SupervisorPage'

function setup() {
  const api = fakeApi()
  renderAt('/supervisor', <SupervisorPage />, api)
  const es = FakeEventSource.last()
  return { api, es }
}

describe('SupervisorPage', () => {
  it('lists pending approvals from the live stream', () => {
    const { es } = setup()
    expect(es.url).toBe('/api/supervisor/events')
    act(() => es.emit('approvals', { approvals: [] }))
    expect(screen.getByText('No pending approvals')).toBeInTheDocument()
    act(() => es.emit('approvals', { approvals: [approval] }))
    const row = screen.getByRole('row', { name: /#W1067251/ })
    expect(within(row).getByText('$1,201.55')).toBeInTheDocument()
    expect(within(row).getByText('Grill')).toBeInTheDocument()
  })

  it('rejects with a note shown to the customer', async () => {
    const { api, es } = setup()
    act(() => es.emit('approvals', { approvals: [approval] }))
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/shown to the customer/)).toBeInTheDocument()
    await userEvent.type(within(dialog).getByRole('textbox'), 'Items look used')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
    expect(api.decide).toHaveBeenCalledWith('c1', 'i9', false, 'Items look used')
  })

  it('shows the conversation read-only', async () => {
    const { api, es } = setup()
    act(() => es.emit('approvals', { approvals: [approval] }))
    await userEvent.click(screen.getByRole('button', { name: 'View chat' }))
    await waitFor(() => expect(api.getChat).toHaveBeenCalledWith('c1'))
    expect(await screen.findByText(view().messages[0].text)).toBeInTheDocument()
  })

  it('marks rows being processed', () => {
    const { es } = setup()
    act(() => es.emit('approvals', { approvals: [{ ...approval, status: 'processing' }] }))
    expect(screen.getByText('Processing…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })
})
