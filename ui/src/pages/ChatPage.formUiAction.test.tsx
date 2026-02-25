import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const {
  fetchPendingTransactions,
  getPendingMerchantAliasesCount,
  sendChatMessage,
  getSpendingReport,
  openPdfFromUrl,
} = vi.hoisted(() => ({
  fetchPendingTransactions: vi.fn(),
  getPendingMerchantAliasesCount: vi.fn(),
  sendChatMessage: vi.fn(),
  getSpendingReport: vi.fn(),
  openPdfFromUrl: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  fetchPendingTransactions,
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases: vi.fn(),
  sendChatMessage,
  importReleves: vi.fn(),
  getSpendingReport,
  hardResetProfile: vi.fn(),
  resetSession: vi.fn(),
  openPdfFromUrl,
  isImportClarificationResult: vi.fn(() => false),
  resolveApiBaseUrl: vi.fn((override?: string) => (override && override.trim().length > 0 ? override.replace(/\/+$/, '') : 'http://127.0.0.1:8000')),
}))

vi.mock('../lib/sessionLifecycle', () => ({
  installSessionResetOnPageExit: vi.fn(() => () => undefined),
  logoutWithSessionReset: vi.fn(),
}))

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(async () => ({ data: { session: { access_token: 'token-123' } } })),
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      refreshSession: vi.fn(async () => ({ data: { session: { access_token: 'token-123' } }, error: null })),
      signOut: vi.fn(),
    },
  },
}))

vi.mock('../components/DebugPanel', () => ({
  DebugPanel: () => null,
}))

describe('ChatPage onboarding form tool_result on split messages', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    fetchPendingTransactions.mockReset()
    getPendingMerchantAliasesCount.mockReset()
    sendChatMessage.mockReset()
    getSpendingReport.mockReset()
    openPdfFromUrl.mockReset()

    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
    fetchPendingTransactions.mockResolvedValue({ count_total: 0, count_twint_p2p_pending: 0, items: [] })
    getSpendingReport.mockResolvedValue({
      period: { start_date: '2026-01-01', end_date: '2026-01-31', label: 'Janvier 2026' },
      currency: 'CHF',
      total: 0,
      count: 0,
      cashflow: { total_income: 0, total_expense: 0, net_cashflow: 0, internal_transfers: 0, net_including_transfers: 0, transaction_count: 0, currency: 'CHF' },
      effective_spending: { outgoing: 0, incoming: 0, net_balance: 0, effective_total: 0 },
      categories: [],
    })
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('keeps onboarding form visible when assistant reply is split in two segments', async () => {
    vi.useFakeTimers()
    try {
      sendChatMessage.mockResolvedValue({
        reply: 'Super, dernière étape.\n\nSélectionne tes banques ci-dessous.',
        tool_result: {
          type: 'ui_action',
          action: 'form',
          form_id: 'onboarding_bank_accounts',
          title: 'Tes banques',
          fields: [
            {
              id: 'selected_banks',
              label: 'Banques utilisées',
              type: 'multi_select',
              required: true,
              options: [{ id: 'ubs', label: 'UBS', value: 'UBS' }],
            },
          ],
          submit_label: 'Valider',
        },
        plan: null,
      })

      await act(async () => {
        createRoot(container).render(<ChatPage email="user@example.com" />)
      })

      await act(async () => {
        await Promise.resolve()
      })

      await act(async () => {
        vi.advanceTimersByTime(300)
      })

      expect(container.querySelector('[aria-label="Formulaire onboarding_bank_accounts"]')).not.toBeNull()

      await act(async () => {
        vi.advanceTimersByTime(1200)
      })

      expect(container.querySelector('[aria-label="Formulaire onboarding_bank_accounts"]')).not.toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
