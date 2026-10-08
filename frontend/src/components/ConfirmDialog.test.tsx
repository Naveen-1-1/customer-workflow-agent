import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { confirmPending } from '@/test/fixtures'
import { ConfirmDialog } from './ConfirmDialog'

const summary = confirmPending.summary!

describe('ConfirmDialog', () => {
  it('shows exactly what will change', () => {
    render(<ConfirmDialog open summary={summary} action="cancel_order" onAnswer={() => {}} />)
    expect(screen.getByText('Cancel order #W1234567')).toBeInTheDocument()
    expect(screen.getByText('Reason: no longer needed')).toBeInTheDocument()
    expect(screen.getByText('Refund: $10.00')).toBeInTheDocument()
  })

  it('cannot be dismissed with Escape', async () => {
    const onAnswer = vi.fn()
    render(<ConfirmDialog open summary={summary} action="cancel_order" onAnswer={onAnswer} />)
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('answers once, even on a double click', async () => {
    const onAnswer = vi.fn()
    render(<ConfirmDialog open summary={summary} action="cancel_order" onAnswer={onAnswer} />)
    const yes = screen.getByRole('button', { name: 'Yes, proceed' })
    await userEvent.dblClick(yes)
    expect(onAnswer).toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenCalledWith(true)
    expect(yes).toBeDisabled()
    expect(screen.getByRole('button', { name: 'No, go back' })).toBeDisabled()
  })

  it('words a transfer offer differently', async () => {
    const onAnswer = vi.fn()
    render(<ConfirmDialog open summary={summary} action="transfer" onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('button', { name: 'No, stay here' }))
    expect(onAnswer).toHaveBeenCalledWith(false)
  })
})
