import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const { getPendingMerchantAliasesCount, resolvePendingMerchantAliases, listBankAccounts } = vi.hoisted(() => ({
  getPendingMerchantAliasesCount: vi.fn(),
  resolvePendingMerchantAliases: vi.fn(),
  listBankAccounts: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases,
  listBankAccounts,
  sendChatMessage: vi.fn(),
  importReleves: vi.fn(),
  hardResetProfile: vi.fn(),
  resetSession: vi.fn(),
  openPdfFromUrl: vi.fn(),
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

function findButtonByText(container: HTMLElement, label: string): HTMLButtonElement | null {
  const buttons = Array.from(container.querySelectorAll('button'))
  return (buttons.find((button) => button.textContent?.includes(label)) as HTMLButtonElement | undefined) ?? null
}

describe('ChatPage pending merchant aliases action', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    getPendingMerchantAliasesCount.mockReset()
    resolvePendingMerchantAliases.mockReset()
    listBankAccounts.mockReset()
    listBankAccounts.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('shows the resolve button when pending_total_count > 0', async () => {
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 3 })

    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(findButtonByText(container, 'Résoudre les marchands restants')).not.toBeNull()
  })

  it('calls resolvePendingMerchantAliases when clicking resolve button', async () => {
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 2 })
    resolvePendingMerchantAliases.mockResolvedValue({
      ok: true,
      type: 'merchant_alias_resolve_result',
      pending_before: 2,
      pending_after: 0,
      batches: 1,
      stats: { applied: 2, failed: 0 },
    })

    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    await act(async () => {
      await Promise.resolve()
    })

    const button = findButtonByText(container, 'Résoudre les marchands restants')
    expect(button).not.toBeNull()

    await act(async () => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(resolvePendingMerchantAliases).toHaveBeenCalledWith({ limit: 20, max_batches: 10 })
  })
})
