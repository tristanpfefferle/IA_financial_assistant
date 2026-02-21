import { act, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { getPendingMerchantAliasesCount } = vi.hoisted(() => ({
  getPendingMerchantAliasesCount: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases: vi.fn(),
  listBankAccounts: vi.fn(),
  sendChatMessage: vi.fn(),
  importReleves: vi.fn(),
  hardResetProfile: vi.fn(),
  resetSession: vi.fn(),
  openPdfFromUrl: vi.fn(),
  isImportClarificationResult: vi.fn(() => false),
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

import { MessageList } from './ChatPage'

describe('MessageList sequential typing cursor', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    container = document.createElement('div')
    document.body.appendChild(container)
    getPendingMerchantAliasesCount.mockReset()
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
    ;(globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__ = true
  })

  afterEach(() => {
    delete (globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__
    document.body.removeChild(container)
  })

  it('starts second assistant message only after first typing completion', async () => {
    vi.useFakeTimers()
    try {
      const root = createRoot(container)
      const messages = [
        { id: 'a1', role: 'assistant' as const, content: 'Premier message très long pour prendre un peu de temps.', createdAt: Date.now() },
        { id: 'a2', role: 'assistant' as const, content: 'Deuxième message.', createdAt: Date.now() + 1 },
      ]

      function Harness() {
        const [typingCursor, setTypingCursor] = useState(0)
        const revealedMessageIdsRef = useRef<Set<string>>(new Set())

        return (
          <MessageList
            messages={messages}
            isLoading={false}
            debugMode={false}
            apiBaseUrl="http://127.0.0.1:8000"
            typingCursor={typingCursor}
            revealedMessageIdsRef={revealedMessageIdsRef}
            messagesRef={{ current: null }}
            onImportNow={() => undefined}
            onScroll={() => undefined}
            onStartConversation={() => undefined}
            onTypingDone={() => setTypingCursor((value) => value + 1)}
          />
        )
      }

      await act(async () => {
        root.render(<Harness />)
      })

      const getContents = () => Array.from(container.querySelectorAll('.message-content')).map((node) => node.textContent ?? '')

      await act(async () => {
        vi.advanceTimersByTime(20)
      })
      let contents = getContents()
      expect(contents[0]?.length).toBeGreaterThan(0)
      expect(contents[0]).not.toContain('Premier message très long pour prendre un peu de temps.')
      expect(contents[1]).toBe('')

      for (let index = 0; index < 200; index += 1) {
        await act(async () => {
          vi.advanceTimersByTime(50)
        })
      }
      contents = getContents()
      expect(contents[0]).toContain('Premier message très long pour prendre un peu de temps.')

      await act(async () => {
        vi.advanceTimersByTime(200)
      })
      contents = getContents()
      expect(contents[1]?.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })
})
