import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const {
  getPendingMerchantAliasesCount,
  listBankAccounts,
  sendChatMessage,
} = vi.hoisted(() => ({
  getPendingMerchantAliasesCount: vi.fn(),
  listBankAccounts: vi.fn(),
  sendChatMessage: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases: vi.fn(),
  listBankAccounts,
  sendChatMessage,
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

describe('ChatPage import intent rendering', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    getPendingMerchantAliasesCount.mockReset()
    listBankAccounts.mockReset()
    sendChatMessage.mockReset()
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
    listBankAccounts.mockResolvedValue({ items: [{ id: 'bank-1', name: 'UBS' }] })
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('shows inline import button and opens dialog only on click', async () => {
    sendChatMessage.mockResolvedValue({
      reply: 'Importe ton relevé quand tu veux.',
      tool_result: {
        type: 'ui_request',
        name: 'import_file',
        bank_account_id: 'bank-1',
        bank_account_name: 'UBS',
        accepted_types: ['csv'],
      },
      plan: null,
    })

    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    await act(async () => {
      await Promise.resolve()
    })

    const startButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Commencer'))
    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const form = container.querySelector('form.composer') as HTMLFormElement
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(container.querySelector('[aria-label="Importer un relevé"]')).toBeNull()

    const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
    expect(inlineButton).toBeTruthy()

    await act(async () => {
      inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(container.querySelector('[aria-label="Importer un relevé"]')).not.toBeNull()
  })
})
