import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { ErrorState, Pagination, Status } from './ui'
describe('operational display states', () => {
  it('does not label an unknown result successful', () => { render(<Status value="UNKNOWN" />); expect(screen.getByText('Unknown')).toBeInTheDocument(); expect(screen.queryByText('Succeeded')).not.toBeInTheDocument() })
  it('shows actionable API failure and retries when requested', () => { const retry = vi.fn(); render(<ErrorState error={new Error('Provider unavailable')} retry={retry} />); expect(screen.getByRole('alert')).toHaveTextContent('Provider unavailable'); fireEvent.click(screen.getByRole('button', { name: 'Try again' })); expect(retry).toHaveBeenCalledOnce() })
  it('prevents navigating beyond the last server page', () => { const change = vi.fn(); render(<Pagination page={2} total={25} pageSize={20} onChange={change} />); expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: 'Previous page' })); expect(change).toHaveBeenCalledWith(1) })
})
