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

  it('shows quick reply yes/no buttons when assistant asks confirmation via ui_action', async () => {
    sendChatMessage
      .mockResolvedValueOnce({
        reply: 'Confirmation requise',
        tool_result: { type: 'ui_action', action: 'quick_replies', options: [{ id: 'yes', label: '✅', value: 'oui' }, { id: 'no', label: '❌', value: 'non' }] },
        plan: null,
      })
      .mockResolvedValueOnce({
        reply: 'Merci pour la confirmation.',
        tool_result: null,
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

    const yesButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === '✅')
    const noButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === '❌')
    expect(yesButton).toBeTruthy()
    expect(noButton).toBeTruthy()

    await act(async () => {
      yesButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(sendChatMessage).toHaveBeenNthCalledWith(2, 'oui', { debug: false })
    expect(container.textContent).toContain('✅')
  })

  it('shows local upload message before assistant import acknowledgement', async () => {
    vi.useFakeTimers()
    try {
      type ImportResult = {
        ok: boolean
        imported_count: number
        transactions_imported_count: number
        bank_account_name: string
        date_range: null
      }

      sendChatMessage
        .mockResolvedValueOnce({
          reply: 'Importe ton fichier.',
          tool_result: {
            type: 'ui_request',
            name: 'import_file',
            accepted_types: ['csv'],
          },
          plan: null,
        })
        .mockResolvedValueOnce({
          reply: 'Analyse en cours.',
          tool_result: null,
          plan: null,
        })

      let resolveImport: ((value: ImportResult) => void) | undefined
      importReleves.mockImplementation(
        () =>
          new Promise<ImportResult>((resolve) => {
            resolveImport = resolve
          }),
      )

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

      const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
      await act(async () => {
        inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      })

      const fileInput = container.querySelector('#import-file-input') as HTMLInputElement
      const file = new File(['date,montant\n2026-01-01,10'], 'transactions.csv', { type: 'text/csv' })
      await act(async () => {
        Object.defineProperty(fileInput, 'files', { value: [file] })
        fileInput.dispatchEvent(new Event('change', { bubbles: true }))
      })

      const dialog = container.querySelector('[aria-label="Importer un relevé"]') as HTMLElement
      const importButton = Array.from(dialog.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Importer')
      await act(async () => {
        importButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })

      expect(container.querySelector('[aria-label="Importer un relevé"]')).toBeNull()
      expect(container.textContent).toContain('Fichier "transactions.csv" envoyé.')
      expect(container.querySelector('[aria-label="import-progress"]')).toBeTruthy()
      expect(container.textContent).toContain('Étape:')

      await act(async () => {
        vi.advanceTimersByTime(800)
      })

      if (!resolveImport) {
        throw new Error('resolveImport not set')
      }

      resolveImport({
        ok: true,
        imported_count: 12,
        transactions_imported_count: 12,
        bank_account_name: 'UBS',
        date_range: null,
      })

      await act(async () => {
        vi.runOnlyPendingTimers()
        await Promise.resolve()
        await Promise.resolve()
        await Promise.resolve()
      })

      const fullText = container.textContent ?? ''
      const uploadIndex = fullText.indexOf('Fichier "transactions.csv" envoyé.')
      const ackIndex = fullText.indexOf('Parfait, j’ai bien reçu ton relevé UBS.')
      expect(uploadIndex).toBeGreaterThanOrEqual(0)
      expect(ackIndex).toBeGreaterThan(uploadIndex)
      expect(fullText).toContain('Parfait, j’ai bien reçu ton relevé UBS.')
    } finally {
      vi.useRealTimers()
    }
  })

  it('enforces CSV-only accept and shows explicit error for PDF upload', async () => {
    sendChatMessage.mockResolvedValue({
      reply: 'Importe ton fichier.',
      tool_result: {
        type: 'ui_request',
        name: 'import_file',
        accepted_types: ['csv', 'pdf'],
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

    const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
    await act(async () => {
      inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const fileInput = container.querySelector('#import-file-input') as HTMLInputElement
    expect(fileInput.getAttribute('accept')).toBe('.csv')
    expect(container.textContent).toContain('Formats acceptés: csv')

    const file = new File(['pdf content'], 'x.pdf', { type: 'application/pdf' })
    await act(async () => {
      Object.defineProperty(fileInput, 'files', { value: [file] })
      fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const dialog = container.querySelector('[aria-label="Importer un relevé"]') as HTMLElement
    const importButton = Array.from(dialog.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Importer')
    await act(async () => {
      importButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    expect(importReleves).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Format invalide. Pour l’instant, seul le format CSV est supporté.')
  })

  it('stops import flow when backend returns ok:false type:error and skips post-import follow-up', async () => {
    vi.useFakeTimers()
    try {
      sendChatMessage.mockResolvedValueOnce({
        reply: 'Importe ton fichier.',
        tool_result: {
          type: 'ui_request',
          name: 'import_file',
          accepted_types: ['csv'],
        },
        plan: null,
      })
      type ImportErrorResult = {
        ok: false
        type: 'error'
        message: string
      }

      let resolveImport: ((value: ImportErrorResult) => void) | undefined
      importReleves.mockImplementation(
        () =>
          new Promise<ImportErrorResult>((resolve) => {
            resolveImport = resolve
          }),
      )

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

      const inlineButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Importer maintenant'))
      await act(async () => {
        inlineButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      })

      const fileInput = container.querySelector('#import-file-input') as HTMLInputElement
      const file = new File(['date,montant\n2026-01-01,10'], 'transactions.csv', { type: 'text/csv' })
      await act(async () => {
        Object.defineProperty(fileInput, 'files', { value: [file] })
        fileInput.dispatchEvent(new Event('change', { bubbles: true }))
      })

      const dialog = container.querySelector('[aria-label="Importer un relevé"]') as HTMLElement
      const importButton = Array.from(dialog.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Importer')
      await act(async () => {
        importButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })

      expect(container.querySelector('[aria-label="Importer un relevé"]')).toBeNull()
      expect(container.querySelector('[aria-label="import-progress"]')).toBeTruthy()

      if (!resolveImport) {
        throw new Error('resolveImport not set')
      }

      resolveImport({
        ok: false,
        type: 'error',
        message: 'Format invalide. Pour l’instant, seul le format CSV est supporté.',
      })

      await act(async () => {
        vi.runOnlyPendingTimers()
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(container.textContent).toContain('Format invalide. Pour l’instant, seul le format CSV est supporté.')
      expect(sendChatMessage).not.toHaveBeenNthCalledWith(2, '', { debug: false })
      expect(sendChatMessage).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })


  it('does not show quick replies before assistant typing is revealed', async () => {
    vi.useFakeTimers()
    ;(globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__ = true
    try {
      sendChatMessage.mockResolvedValue({
        reply: 'Message assez long pour déclencher un typing progressif sur plusieurs ticks.',
        tool_result: { type: 'ui_action', action: 'quick_replies', options: [{ id: 'yes', label: '✅', value: 'oui' }, { id: 'no', label: '❌', value: 'non' }] },
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

      let yesButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === '✅')
      expect(yesButton).toBeFalsy()

      await act(async () => {
        vi.advanceTimersByTime(300)
      })

      yesButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === '✅')
      expect(yesButton).toBeFalsy()
    } finally {
      delete (globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__
      vi.useRealTimers()
    }
  })

  it('auto-scrolls to bottom on new messages and during typing', async () => {
    vi.useFakeTimers()
    ;(globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__ = true
    try {
      sendChatMessage.mockResolvedValue({
        reply: 'Texte long pour vérifier le défilement automatique pendant la saisie de la réponse assistant.',
        tool_result: null,
        plan: null,
      })

      await act(async () => {
        createRoot(container).render(<ChatPage email="user@example.com" />)
      })
      await act(async () => {
        await Promise.resolve()
      })

      const messagesEl = container.querySelector('.messages') as HTMLDivElement
      Object.defineProperty(messagesEl, 'scrollHeight', { configurable: true, get: () => 1200 })
      Object.defineProperty(messagesEl, 'clientHeight', { configurable: true, get: () => 300 })
      let scrollTopValue = 0
      Object.defineProperty(messagesEl, 'scrollTop', {
        configurable: true,
        get: () => scrollTopValue,
        set: (value: number) => {
          scrollTopValue = value
        },
      })

      const startButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Commencer'))
      await act(async () => {
        startButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })

      expect(scrollTopValue).toBe(1200)

      await act(async () => {
        vi.advanceTimersByTime(400)
      })
      expect(scrollTopValue).toBe(1200)
    } finally {
      delete (globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__
      vi.useRealTimers()
    }
  })

})
