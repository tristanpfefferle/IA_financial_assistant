import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(async () => ({
        data: {
          session: {
            access_token: 'token-123',
          },
        },
      })),
      refreshSession: vi.fn(async () => ({ data: { session: null }, error: null })),
    },
  },
}))

import {
  __unsafeResetSessionResetStateForTests,
  getSpendingReport,
  hardResetProfile,
  openPdfFromUrl,
  resetSession,
} from './agentApi'

describe('resetSession', () => {
  beforeEach(() => {
    __unsafeResetSessionResetStateForTests()
    vi.restoreAllMocks()
  })

  it('sends at most one request per lifecycle', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await resetSession()
    await resetSession()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent/reset-session'),
      expect.objectContaining({ method: 'POST' }),
    )
  })
})

describe('hardResetProfile', () => {
  it('posts debug hard reset payload with confirm=true', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await hardResetProfile()

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/debug/hard-reset'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ confirm: true }),
      }),
    )
  })
})

describe('openPdfFromUrl', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('falls back to window.open with access_token when fetch fails with TypeError', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    vi.stubGlobal('fetch', fetchMock)
    const windowOpenSpy = vi.spyOn(window, 'open').mockReturnValue(null)

    await expect(openPdfFromUrl('http://127.0.0.1:8000/finance/reports/spending.pdf')).resolves.toBeUndefined()

    expect(windowOpenSpy).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/finance/reports/spending.pdf?access_token=token-123',
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('masks access_token in debug logs when fallback appends query token', async () => {
    vi.stubEnv('VITE_UI_DEBUG', 'true')
    const fetchMock = vi.fn(async () => {
      throw new TypeError('NetworkError')
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'open').mockReturnValue(null)
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => undefined)

    await openPdfFromUrl('http://127.0.0.1:8000/finance/reports/spending.pdf')

    const logs = debugSpy.mock.calls.flat().map(String).join(' ')
    expect(logs).toContain('access_token=***')
    expect(logs).not.toContain('access_token=token-123')
  })
})

describe('getSpendingReport', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('builds an absolute URL from api base URL', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          period: { start_date: '2026-01-01', end_date: '2026-01-31', label: 'Jan 2026' },
          currency: 'CHF',
          total: '10.00',
          count: 1,
          cashflow: {
            total_income: '0',
            total_expense: '10',
            net_cashflow: '-10',
            internal_transfers: '0',
            net_including_transfers: '-10',
            transaction_count: 1,
            currency: 'CHF',
          },
          effective_spending: { outgoing: '10', incoming: '0', net_balance: '-10', effective_total: '10' },
          categories: [],
        }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getSpendingReport({ month: '2026-01' }, 'http://127.0.0.1:8000')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/finance/reports/spending?month=2026-01',
      expect.objectContaining({ method: 'GET' }),
    )
  })
})
