import { act } from 'react'
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

describe('MessageList typing indicator behavior', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    container = document.createElement('div')
    document.body.appendChild(container)
    getPendingMerchantAliasesCount.mockReset()
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('shows typing indicator while waiting for assistant response', async () => {
    await act(async () => {
      createRoot(container).render(
        <MessageList
          messages={[]}
          isLoading={true}
          isAssistantTyping={true}
          debugMode={false}
          apiBaseUrl="http://127.0.0.1:8000"
          onImportNow={() => undefined}
        />,
      )
    })

    const typingDots = container.querySelector('.typing-dots')
    expect(typingDots).not.toBeNull()
    expect(typingDots?.getAttribute('aria-label')).toBe('L’assistant écrit')
    expect(container.textContent).not.toContain('L’assistant écrit')
  })

  it('hides typing indicator and renders the complete assistant message at once', async () => {
    const fullMessage = 'Message final complet sans rendu progressif.'

    await act(async () => {
      createRoot(container).render(
        <MessageList
          messages={[{ id: 'assistant-1', role: 'assistant', content: fullMessage, createdAt: Date.now() }]}
          isLoading={false}
          isAssistantTyping={false}
          debugMode={false}
          apiBaseUrl="http://127.0.0.1:8000"
          onImportNow={() => undefined}
        />,
      )
    })

    expect(container.textContent).toContain(fullMessage)
    expect(container.textContent).not.toContain('L’assistant écrit')
  })
})
