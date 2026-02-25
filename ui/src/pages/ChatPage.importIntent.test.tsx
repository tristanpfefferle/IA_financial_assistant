import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const {
  fetchPendingTransactions,
  getPendingMerchantAliasesCount,
  importReleves,
  getSpendingReport,
  openPdfFromUrl,
  sendChatMessage,
} = vi.hoisted(() => ({
  fetchPendingTransactions: vi.fn(),
  getPendingMerchantAliasesCount: vi.fn(),
  importReleves: vi.fn(),
  getSpendingReport: vi.fn(),
  openPdfFromUrl: vi.fn(),
  sendChatMessage: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  fetchPendingTransactions,
  getPendingMerchantAliasesCount,
  resolvePendingMerchantAliases: vi.fn(),
  isImportClarificationResult: (value: unknown) => {
    if (!value || typeof value !== 'object') return false
    return (value as { ok?: unknown; type?: unknown }).ok === false && (value as { type?: unknown }).type === 'clarification'
  },
  sendChatMessage,
  importReleves,
  getSpendingReport,
  hardResetProfile: vi.fn(),
  resetSession: vi.fn(),
  openPdfFromUrl,
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
    fetchPendingTransactions.mockReset()
    getPendingMerchantAliasesCount.mockReset()
    importReleves.mockReset()
    sendChatMessage.mockReset()
    getSpendingReport.mockReset()
    openPdfFromUrl.mockReset()
    openPdfFromUrl.mockResolvedValue(undefined)
    getSpendingReport.mockResolvedValue({
      period: { start_date: '2026-01-01', end_date: '2026-01-31', label: 'Janvier 2026' },
      currency: 'CHF',
      total: 0,
      count: 0,
      cashflow: {
        total_income: 0,
        total_expense: 0,
        net_cashflow: 0,
        internal_transfers: 0,
        net_including_transfers: 0,
        transaction_count: 0,
        currency: 'CHF',
      },
      effective_spending: {
        outgoing: 0,
        incoming: 0,
        net_balance: 0,
        effective_total: 0,
      },
      categories: [],
    })
    getPendingMerchantAliasesCount.mockResolvedValue({ pending_total_count: 0 })
    fetchPendingTransactions.mockResolvedValue({ count_total: 0, count_twint_p2p_pending: 0, items: [] })
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



  it('shows TWINT pending categorization badge and assistant prompt after import', async () => {
    sendChatMessage
      .mockResolvedValueOnce({
        reply: 'Salut 👋',
        tool_result: { type: 'ui_request', name: 'import_file', accepted_types: ['csv'] },
        plan: null,
      })
      .mockResolvedValueOnce({
        reply: 'Import analysé.',
        tool_result: null,
        plan: null,
      })

    importReleves.mockResolvedValue({
      imported_count: 1,
      failed_count: 0,
      duplicates_count: 0,
      replaced_count: 0,
      identical_count: 0,
      modified_count: 0,
      new_count: 1,
      requires_confirmation: false,
      errors: [],
      preview: [],
    })

    fetchPendingTransactions.mockResolvedValue({ count_total: 2, count_twint_p2p_pending: 2, items: [] })

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

    expect(container.textContent).toContain('À catégoriser (TWINT): 2')
    expect(container.textContent).toContain('J’ai détecté 2 paiements TWINT à catégoriser')
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


  it('renders onboarding name form card and submits deterministic special message', async () => {
    sendChatMessage
      .mockResolvedValueOnce({
        reply: 'Renseigne ton prénom et ton nom.',
        tool_result: {
          type: 'ui_action',
          action: 'form',
          form_id: 'onboarding_profile_name',
          title: 'Ton profil',
          fields: [
            { id: 'first_name', label: 'Prénom', type: 'text', required: true, placeholder: 'Tristan' },
            { id: 'last_name', label: 'Nom', type: 'text', required: true, placeholder: 'Pfefferlé' },
          ],
          submit_label: 'Valider',
        },
        plan: null,
      })
      .mockResolvedValueOnce({
        reply: 'Quelle est ta date de naissance ?',
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

    const firstNameInput = Array.from(container.querySelectorAll('input')).find((input) => input.getAttribute('placeholder') === 'Tristan') as HTMLInputElement
    const lastNameInput = Array.from(container.querySelectorAll('input')).find((input) => input.getAttribute('placeholder') === 'Pfefferlé') as HTMLInputElement
    expect(firstNameInput).toBeTruthy()
    expect(lastNameInput).toBeTruthy()

    expect(container.querySelector('textarea[aria-label="Message"]')).toBeNull()

    await act(async () => {
      firstNameInput.value = 'Tristan'
      firstNameInput.dispatchEvent(new Event('change', { bubbles: true }))
      lastNameInput.value = 'Pfefferlé'
      lastNameInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const submitButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Valider')
    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(sendChatMessage).toHaveBeenNthCalledWith(
      2,
      `Je m'appelle Tristan Pfefferlé.\n__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Tristan","last_name":"Pfefferlé"}}`,
      { debug: false },
    )
    expect(container.textContent).toContain("Je m'appelle Tristan Pfefferlé.")
    expect(container.textContent).not.toContain('__ui_form_submit__:')
  })

  it('renders onboarding birth-date form card and submits deterministic special message', async () => {
    sendChatMessage
      .mockResolvedValueOnce({
        reply: 'Quelle est ta date de naissance ?',
        tool_result: {
          type: 'ui_action',
          action: 'form',
          form_id: 'onboarding_profile_birth_date',
          title: 'Date de naissance',
          fields: [{ id: 'birth_date', label: 'Date de naissance', type: 'date', required: true }],
          submit_label: 'Valider',
        },
        plan: null,
      })
      .mockResolvedValueOnce({
        reply: 'Parfait ✅',
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

    const birthDateInput = container.querySelector('input[type="date"]') as HTMLInputElement
    expect(birthDateInput).toBeTruthy()

    expect(container.querySelector('textarea[aria-label="Message"]')).toBeNull()

    await act(async () => {
      birthDateInput.value = '2001-12-22'
      birthDateInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const submitButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === 'Valider')
    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(sendChatMessage).toHaveBeenNthCalledWith(
      2,
      'Je suis né le 2001-12-22.\n__ui_form_submit__:{"form_id":"onboarding_profile_birth_date","values":{"birth_date":"2001-12-22"}}',
      { debug: false },
    )
    expect(container.textContent).toContain('Je suis né le 2001-12-22.')
    expect(container.textContent).not.toContain('__ui_form_submit__:')
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
      expect(sendChatMessage).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })


  it('shows quick replies and hides text input when quick reply mode is active', async () => {
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
      expect(yesButton).toBeTruthy()
      expect(container.querySelector('textarea[aria-label="Message"]')).toBeNull()

      await act(async () => {
        vi.advanceTimersByTime(300)
      })

      yesButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.trim() === '✅')
      expect(yesButton).toBeTruthy()
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
