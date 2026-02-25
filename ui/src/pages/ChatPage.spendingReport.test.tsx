import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-virtuoso', async () => {
  const React = await vi.importActual<typeof import('react')>('react')

  const Virtuoso = React.forwardRef(function MockVirtuoso(props: {
    className?: string
    style?: React.CSSProperties
    data?: unknown[]
    itemContent: (index: number, item: unknown) => React.ReactNode
    components?: { Header?: React.ComponentType; Footer?: React.ComponentType }
    atBottomStateChange?: (isBottom: boolean) => void
  }, ref: React.ForwardedRef<{ scrollToIndex: () => void }>) {
    const { className, style, data = [], itemContent, components, atBottomStateChange } = props

    React.useImperativeHandle(ref, () => ({
      scrollToIndex: () => undefined,
    }))

    React.useEffect(() => {
      atBottomStateChange?.(true)
    }, [atBottomStateChange])

    const Header = components?.Header
    const Footer = components?.Footer

    return (
      <div className={className} style={style}>
        {Header ? <Header /> : null}
        {data.map((item, index) => (
          <div key={index}>{itemContent(index, item)}</div>
        ))}
        {Footer ? <Footer /> : null}
      </div>
    )
  })

  return {
    Virtuoso,
  }
})

import { MessageList } from './ChatPage'
import { getSpendingReport } from '../api/agentApi'

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(async () => ({ data: { session: { access_token: 'token-123' } } })),
      refreshSession: vi.fn(async () => ({ data: { session: { access_token: 'token-123' } }, error: null })),
    },
  },
}))

vi.mock('../api/agentApi', async () => {
  const actual = await vi.importActual<typeof import('../api/agentApi')>('../api/agentApi')
  return {
    ...actual,
    getSpendingReport: vi.fn(),
    openPdfFromUrl: vi.fn(),
  }
})

describe('MessageList spending report summary', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    vi.mocked(getSpendingReport).mockReset()
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('renders shared spending and effective total from JSON report', async () => {
    vi.mocked(getSpendingReport).mockResolvedValue({
      period: { start_date: '2026-01-01', end_date: '2026-01-31', label: 'Janvier 2026' },
      currency: 'CHF',
      total: 1234.56,
      count: 42,
      cashflow: {
        total_income: 0,
        total_expense: 0,
        net_cashflow: 0,
        internal_transfers: 0,
        net_including_transfers: 0,
        transaction_count: 42,
        currency: 'CHF',
      },
      effective_spending: {
        outgoing: 100,
        incoming: 25,
        net_balance: 75,
        effective_total: 1309.56,
      },
      categories: [],
    })

    await act(async () => {
      createRoot(container).render(
        <MessageList
          messages={[
            {
              id: 'assistant-1',
              role: 'assistant',
              content: 'Rapport prêt.',
              createdAt: Date.now(),
              toolResult: {
                type: 'ui_request',
                name: 'open_pdf_report',
                url: '/finance/reports/spending.pdf?month=2026-01',
              },
            },
          ]}
          isLoading={false}
          typingDotsVisible={false}
          debugMode={false}
          apiBaseUrl="http://127.0.0.1:8000"
          onImportNow={() => undefined}
        />,
      )
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Total effectif')
    expect(container.textContent).toContain('Partage sortant')
    expect(container.textContent).toContain('Partage entrant')
  })

  it('renders JSON error fallback and keeps PDF button when report loading fails', async () => {
    vi.mocked(getSpendingReport).mockRejectedValue(new Error('JSON parsing failed'))

    await act(async () => {
      createRoot(container).render(
        <MessageList
          messages={[
            {
              id: 'assistant-2',
              role: 'assistant',
              content: 'Rapport prêt.',
              createdAt: Date.now(),
              toolResult: {
                type: 'ui_request',
                name: 'open_pdf_report',
                url: '/finance/reports/spending.pdf?month=2026-01',
              },
            },
          ]}
          isLoading={false}
          typingDotsVisible={false}
          debugMode={false}
          apiBaseUrl="http://127.0.0.1:8000"
          onImportNow={() => undefined}
        />,
      )
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(container.textContent).toContain('Impossible de charger le rapport détaillé. Utilise le PDF.')
    expect(container.textContent).toContain('Ouvrir PDF')
  })
})
