import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react'

import { importReleves, resetSession, sendChatMessage, type RelevesImportResult } from '../api/agentApi'
import { installSessionResetOnPageExit, logoutWithSessionReset } from '../lib/sessionLifecycle'
import { supabase } from '../lib/supabaseClient'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolResult?: Record<string, unknown> | null
  plan?: Record<string, unknown> | null
}

type ChatPageProps = {
  email?: string
}

type ImportUiRequest = {
  type: 'ui_request'
  name: 'import_file'
  bank_account_id: string
  bank_account_name?: string
  accepted_types?: string[]
}

type ImportSuccessState = {
  transactionsImported: number
  dateRange: { start: string; end: string } | null
  bankAccountName?: string | null
}

function toImportUiRequest(value: unknown): ImportUiRequest | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_request' || record.name !== 'import_file') {
    return null
  }

  const bankAccountId = record.bank_account_id
  if (typeof bankAccountId !== 'string' || !bankAccountId) {
    return null
  }

  const acceptedTypes = Array.isArray(record.accepted_types)
    ? record.accepted_types.filter((type): type is string => typeof type === 'string')
    : ['csv', 'pdf']

  return {
    type: 'ui_request',
    name: 'import_file',
    bank_account_id: bankAccountId,
    bank_account_name: typeof record.bank_account_name === 'string' ? record.bank_account_name : undefined,
    accepted_types: acceptedTypes,
  }
}

function formatDateRange(dateRange: { start: string; end: string } | null): string {
  if (!dateRange) {
    return 'Période détectée: non disponible'
  }
  return `Période détectée: ${dateRange.start} → ${dateRange.end}`
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string') {
        reject(new Error('Contenu de fichier invalide'))
        return
      }
      const commaIndex = result.indexOf(',')
      if (commaIndex < 0) {
        reject(new Error('Encodage base64 invalide'))
        return
      }
      resolve(result.slice(commaIndex + 1))
    }
    reader.onerror = () => reject(new Error('Lecture fichier impossible'))
    reader.readAsDataURL(file)
  })
}

export function ChatPage({ email }: ChatPageProps) {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [isRefreshingSession, setIsRefreshingSession] = useState(false)
  const [pendingUiRequest, setPendingUiRequest] = useState<ImportUiRequest | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const [importSuccess, setImportSuccess] = useState<ImportSuccessState | null>(null)
  const envDebugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const [debugMode, setDebugMode] = useState(false)
  const apiBaseUrl = useMemo(() => {
    const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
    return rawBaseUrl.replace(/\/+$/, '')
  }, [])
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const assistantMessagesCount = useMemo(
    () => messages.filter((chatMessage) => chatMessage.role === 'assistant').length,
    [messages],
  )

  useEffect(() => {
    const storedDebugMode = localStorage.getItem('chat_debug_mode')
    setDebugMode(storedDebugMode === '1')

    let active = true

    supabase.auth.getSession().then(({ data }) => {
      if (active) {
        setHasToken(Boolean(data.session?.access_token))
      }
    })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setHasToken(Boolean(session?.access_token))
    })

    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [])

  useEffect(() => {
    localStorage.setItem('chat_debug_mode', debugMode ? '1' : '0')
  }, [debugMode])

  useEffect(() => {
    if (!hasToken) {
      return
    }

    const cleanup = installSessionResetOnPageExit(() => {
      void resetSession({ keepalive: true, timeoutMs: 1500 })
    })

    return cleanup
  }, [hasToken])

  const isConnected = useMemo(() => Boolean(email), [email])
  const canSubmit = message.trim().length > 0 && !isLoading
  const hasUnauthorizedError = useMemo(() => error?.includes('(401)') ?? false, [error])
  const fileAccept = useMemo(() => {
    const acceptedTypes = pendingUiRequest?.accepted_types
    if (!acceptedTypes || acceptedTypes.length === 0) {
      return '.csv,.pdf'
    }

    const extensions = acceptedTypes
      .map((type) => type.trim().replace(/^\./, '').toLowerCase())
      .filter((type) => type.length > 0)
      .map((type) => `.${type}`)

    return extensions.length > 0 ? extensions.join(',') : '.csv,.pdf'
  }, [pendingUiRequest])

  useEffect(() => {
    if (!assistantMessagesCount) {
      return
    }

    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [assistantMessagesCount])

  async function handleImportFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !pendingUiRequest || isImporting) {
      return
    }

    const allowedExtensions = (pendingUiRequest.accepted_types ?? ['csv', 'pdf']).map((item) => item.toLowerCase())
    const extension = file.name.split('.').pop()?.toLowerCase()
    if (!extension || !allowedExtensions.includes(extension)) {
      setImportError('Format invalide. Sélectionne un fichier CSV ou PDF.')
      event.target.value = ''
      return
    }

    setImportError(null)
    setImportSuccess(null)
    setIsImporting(true)

    try {
      const contentBase64 = await readFileAsBase64(file)
      const result: RelevesImportResult = await importReleves({
        files: [{ filename: file.name, content_base64: contentBase64 }],
        bank_account_id: pendingUiRequest.bank_account_id,
        import_mode: 'commit',
        modified_action: 'replace',
      })

      const typedResult = result as RelevesImportResult & {
        transactions_imported_count?: number
        transactions_imported?: number
        date_range?: { start: string; end: string } | null
        bank_account_name?: string | null
      }
      const importedCount = typedResult.transactions_imported_count ?? typedResult.transactions_imported ?? result.imported_count
      setImportSuccess({
        transactionsImported: importedCount,
        dateRange: typedResult.date_range ?? null,
        bankAccountName: typedResult.bank_account_name ?? pendingUiRequest.bank_account_name,
      })
      setPendingUiRequest(null)
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      setImportError(errorMessage)
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      setIsImporting(false)
    }
  }

  async function handleLogout() {
    setError(null)

    await logoutWithSessionReset({
      resetSession: () => resetSession({ timeoutMs: 1500 }),
      signOut: () => supabase.auth.signOut(),
      onLogoutError: () => {
        setError('Impossible de vous déconnecter pour le moment. Veuillez réessayer.')
      },
    })
  }

  async function handleRefreshSession() {
    if (isRefreshingSession) {
      return
    }

    setIsRefreshingSession(true)
    setError(null)

    try {
      const { data, error: refreshError } = await supabase.auth.refreshSession()
      if (refreshError || !data.session?.access_token) {
        setError('Rafraîchissement de session impossible. Veuillez vous déconnecter puis vous reconnecter.')
      }
    } finally {
      setIsRefreshingSession(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedMessage = message.trim()
    if (!trimmedMessage || isLoading) {
      return
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: trimmedMessage,
    }

    setMessages((previousMessages) => [...previousMessages, userMessage])
    setMessage('')
    setError(null)
    setIsLoading(true)

    try {
      const response = await sendChatMessage(trimmedMessage, { debug: debugMode })
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: response.reply,
        toolResult: response.tool_result,
        plan: response.plan,
      }
      setMessages((previousMessages) => [...previousMessages, assistantMessage])
      const parsedUiRequest = toImportUiRequest(response.tool_result)
      if (parsedUiRequest) {
        setPendingUiRequest(parsedUiRequest)
        setImportError(null)
      }
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      setError(errorMessage)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="chat-shell">
      <section className="chat-card">
        <header className="chat-header">
          <h1>Assistant financier IA</h1>
          <button type="button" className="secondary-button" onClick={handleLogout}>
            Se déconnecter
          </button>
        </header>

        {envDebugEnabled ? (
          <div className="debug-banner" role="status" aria-live="polite">
            Connecté: {isConnected ? 'oui' : 'non'} | Email: {email ?? 'inconnu'} | Token: {hasToken ? 'présent' : 'absent'} |
            API: {apiBaseUrl}
          </div>
        ) : null}

        <section className="import-panel">
          <h2>Paramètres du chat</h2>
          <label>
            <input type="checkbox" checked={debugMode} onChange={(event) => setDebugMode(event.target.checked)} /> Debug
          </label>
        </section>

        {pendingUiRequest ? (
          <section className="import-panel" aria-live="polite">
            <h2>Import du relevé</h2>
            <p className="placeholder-text">
              Compte: {pendingUiRequest.bank_account_name || pendingUiRequest.bank_account_id}
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept={fileAccept}
              onChange={handleImportFileSelection}
              style={{ display: 'none' }}
              disabled={isImporting}
            />
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={isImporting}>
              Parcourir...
            </button>
            {isImporting ? <progress aria-label="Import en cours" /> : null}
            {isImporting ? <p className="placeholder-text">⏳ Import en cours...</p> : null}
            {importError ? <p className="error-text">{importError}</p> : null}
          </section>
        ) : null}

        {importSuccess ? (
          <section className="import-panel" aria-live="polite">
            <p>
              ✅ Import OK {importSuccess.bankAccountName ? `(${importSuccess.bankAccountName})` : ''}
            </p>
            <p>Transactions importées: {importSuccess.transactionsImported}</p>
            <p>{formatDateRange(importSuccess.dateRange)}</p>
          </section>
        ) : null}

        <div className="messages" aria-live="polite" ref={messagesRef}>
          {messages.length === 0 ? <p className="placeholder-text">Commencez la conversation avec l’IA.</p> : null}
          {messages.map((chatMessage) => (
            <article key={chatMessage.id} className={`message message-${chatMessage.role}`}>
              <p className="message-role">{chatMessage.role === 'user' ? 'Vous' : 'Assistant'}</p>
              <p>{chatMessage.content}</p>
              {debugMode && chatMessage.role === 'assistant' && chatMessage.plan ? (
                (() => {
                  const plan = chatMessage.plan as Record<string, unknown>
                  const planToolName = typeof plan['tool_name'] === 'string' ? plan['tool_name'] : null
                  const planPayload = plan['payload']
                  const planMeta = plan['meta']
                  const memoryInjected =
                    planMeta && typeof planMeta === 'object'
                      ? (planMeta as Record<string, unknown>)['debug_memory_injected']
                      : undefined

                  return (
                    <details>
                      <summary>Debug</summary>
                      {planToolName ? <p>Tool: {planToolName}</p> : null}
                      {planPayload !== undefined ? (
                        <>
                          <p>Payload:</p>
                          <pre>{JSON.stringify(planPayload, null, 2)}</pre>
                        </>
                      ) : null}
                      {planMeta !== undefined ? (
                        <>
                          <p>Meta:</p>
                          <pre>{JSON.stringify(planMeta, null, 2)}</pre>
                        </>
                      ) : null}
                      {memoryInjected !== undefined ? (
                        <>
                          <p>Memory injected:</p>
                          <pre>{JSON.stringify(memoryInjected, null, 2)}</pre>
                        </>
                      ) : null}
                    </details>
                  )
                })()
              ) : null}
            </article>
          ))}
          {isLoading ? <p className="placeholder-text">Envoi...</p> : null}
        </div>

        <form onSubmit={handleSubmit} className="chat-form">
          <input
            type="text"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Posez une question sur vos finances..."
            aria-label="Message"
          />
          <button type="submit" disabled={!canSubmit}>
            {isLoading ? 'Envoi...' : 'Envoyer'}
          </button>
        </form>

        {error ? <p className="error-text">{error}</p> : null}
        {hasUnauthorizedError && isConnected ? (
          <div>
            <button type="button" className="secondary-button" onClick={handleRefreshSession} disabled={isRefreshingSession}>
              {isRefreshingSession ? 'Rafraîchissement...' : 'Rafraîchir la session'}
            </button>
            <p className="placeholder-text">Si le problème persiste, reconnectez-vous pour renouveler vos identifiants.</p>
          </div>
        ) : null}
      </section>
    </main>
  )
}
