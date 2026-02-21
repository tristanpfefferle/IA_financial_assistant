import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const {
  getPendingMerchantAliasesCount,
  importReleves,
  openPdfFromUrl,
  sendChatMessage,
} = vi.hoisted(() => ({
  getPendingMerchantAliasesCount: vi.fn(),
  importReleves: vi.fn(),
  openPdfFromUrl: vi.fn(),
  sendChatMessage: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases: vi.fn(),
  isImportClarificationResult: (value: unknown) => {
    if (!value || typeof value !== 'object') return false
    return (value as { ok?: unknown; type?: unknown }).ok === false && (value as { type?: unknown }).type === 'clarification'
  },
  sendChatMessage,
  importReleves,
  hardResetProfile: vi.fn(),
  resetSession: vi.fn(),
  openPdfFromUrl,
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
  const originalFileReader = globalThis.FileReader

  class MockFileReader {
    result: string | ArrayBuffer | null = null
    onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null
    onerror: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null

    readAsDataURL(_file: Blob): void {
      this.result = 'data:text/csv;base64,ZHVtbXk='
      this.onload?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>)
    }
  }

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    Object.defineProperty(globalThis, 'FileReader', { value: MockFileReader, configurable: true })
    getPendingMerchantAliasesCount.mockReset()
    importReleves.mockReset()
    sendChatMessage.mockReset()
    openPdfFromUrl.mockReset()
    openPdfFromUrl.mockResolvedValue(undefined)
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
  })

  afterEach(() => {
    Object.defineProperty(globalThis, 'FileReader', { value: originalFileReader, configurable: true })
    document.body.removeChild(container)
  })

  it('segments assistant greeting and then shows import CTA', async () => {
    vi.useFakeTimers()
    try {
      sendChatMessage.mockResolvedValue({
        reply: 'Premier paragraphe\n\nDeuxième paragraphe\n\nTroisième paragraphe.',
        tool_result: {
          type: 'ui_request',
          name: 'import_file',
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
        await Promise.resolve()
      })

      expect(sendChatMessage).toHaveBeenCalledWith('', { debug: false, requestGreeting: true })

      await act(async () => {
        vi.advanceTimersByTime(300)
      })
      expect(container.textContent).toContain('Premier paragraphe')
      expect(container.textContent).not.toContain('Deuxième paragraphe')
      expect(container.textContent).not.toContain('Troisième paragraphe')

      await act(async () => {
        vi.advanceTimersByTime(1300)
      })
      expect(container.textContent).toContain('Deuxième paragraphe')
      expect(container.textContent).not.toContain('Troisième paragraphe')

      await act(async () => {
        vi.advanceTimersByTime(2400)
      })
      expect(container.textContent).toContain('Troisième paragraphe')

      const fullText = container.textContent ?? ''
      const firstIndex = fullText.indexOf('Premier paragraphe')
      const secondIndex = fullText.indexOf('Deuxième paragraphe')
      const thirdIndex = fullText.indexOf('Troisième paragraphe')
      expect(firstIndex).toBeGreaterThanOrEqual(0)
      expect(secondIndex).toBeGreaterThan(firstIndex)
      expect(thirdIndex).toBeGreaterThan(secondIndex)

      const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
      expect(inlineButton).toBeTruthy()

      await act(async () => {
        inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      })

      expect(container.querySelector('[aria-label="Importer un relevé"]')).not.toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('closes dialog and appends assistant clarification message when import needs clarification', async () => {
    sendChatMessage.mockResolvedValue({
      reply: 'Salut 👋',
      tool_result: {
        type: 'ui_request',
        name: 'import_file',
        accepted_types: ['csv'],
      },
      plan: null,
    })
    importReleves.mockResolvedValue({
      ok: false,
      type: 'clarification',
      message: 'J’ai trouvé plusieurs comptes: UBS / Revolut. Lequel ?',
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
      await Promise.resolve()
    })

    const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
    await act(async () => {
      inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const fileInput = container.querySelector('#import-file-input') as HTMLInputElement
    const file = new File(['date,montant\n2026-01-01,10'], 'releve.csv', { type: 'text/csv' })
    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file] })
      fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const dialog = container.querySelector('[aria-label="Importer un relevé"]') as HTMLElement
    const importButton = Array.from(dialog.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Importer')
    await act(async () => {
      importButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(importReleves).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('J’ai trouvé plusieurs comptes: UBS / Revolut. Lequel ?')
    expect(container.textContent).not.toContain('Parfait, j’ai bien reçu ton relevé')
  })

  it('renders clickable PDF controls and re-opens PDF on click', async () => {
    sendChatMessage.mockResolvedValue({
      reply: 'Rapport prêt.',
      tool_result: {
        type: 'ui_request',
        name: 'open_pdf_report',
        url: '/finance/reports/spending.pdf',
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
      await Promise.resolve()
      await Promise.resolve()
    })

    const pdfElements = Array.from(container.querySelectorAll('button, span')).filter((element) => element.textContent?.trim() === 'PDF')
    expect(pdfElements.length).toBeGreaterThan(0)

    await act(async () => {
      pdfElements[0]?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(openPdfFromUrl).toHaveBeenCalled()
  })
})
