import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react'

import {
  importReleves,
  listBankAccounts,
  sendChatMessage,
  type BankAccount,
  type ImportFilePayload,
  type RelevesImportResult,
} from '../api/agentApi'
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

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSize, bytes.length))
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

export function ChatPage({ email }: ChatPageProps) {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [isRefreshingSession, setIsRefreshingSession] = useState(false)
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [selectedBankAccountId, setSelectedBankAccountId] = useState<string>('')
  const [selectedFile, setSelectedFile] = useState<ImportFilePayload | null>(null)
  const [importMode, setImportMode] = useState<'analyze' | 'commit'>('analyze')
  const [modifiedAction, setModifiedAction] = useState<'keep' | 'replace'>('replace')
  const [importResult, setImportResult] = useState<RelevesImportResult | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const envDebugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const [debugMode, setDebugMode] = useState(false)
  const apiBaseUrl = useMemo(() => {
    const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
    return rawBaseUrl.replace(/\/+$/, '')
  }, [])
  const messagesRef = useRef<HTMLDivElement | null>(null)
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

    listBankAccounts()
      .then((result) => {
        setBankAccounts(result.items ?? [])
      })
      .catch((caughtError) => {
        const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
        setImportError(errorMessage)
      })
  }, [hasToken])

  const isConnected = useMemo(() => Boolean(email), [email])
  const canSubmit = message.trim().length > 0 && !isLoading
  const hasUnauthorizedError = useMemo(() => error?.includes('(401)') ?? false, [error])
  const canImport = useMemo(() => Boolean(selectedFile) && !isImporting, [selectedFile, isImporting])

  useEffect(() => {
    if (!assistantMessagesCount) {
      return
    }

    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [assistantMessagesCount])

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) {
      setSelectedFile(null)
      return
    }

    setImportError(null)
    setImportResult(null)

    try {
      const arrayBuffer = await file.arrayBuffer()
      setSelectedFile({
        filename: file.name,
        content_base64: arrayBufferToBase64(arrayBuffer),
      })
    } catch {
      setSelectedFile(null)
      setImportError('Impossible de lire le fichier sélectionné.')
    }
  }

  async function runImport(mode: 'analyze' | 'commit', action: 'keep' | 'replace') {
    if (!selectedFile || isImporting) {
      return
    }

    setIsImporting(true)
    setImportError(null)
    setImportResult(null)

    try {
      const result = await importReleves({
        files: [selectedFile],
        bank_account_id: selectedBankAccountId || null,
        import_mode: mode,
        modified_action: action,
      })
      setImportMode(mode)
      setModifiedAction(action)
      setImportResult(result)
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      setImportError(errorMessage)
    } finally {
      setIsImporting(false)
    }
  }

  async function handleLogout() {
    setError(null)

    try {
      const { error: signOutError } = await supabase.auth.signOut()
      if (signOutError) {
        throw signOutError
      }
    } catch {
      setError('Impossible de vous déconnecter pour le moment. Veuillez réessayer.')
    }
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

        <section className="import-panel">
          <h2>Importer un relevé (CSV)</h2>
          <div className="import-controls">
            <input type="file" accept=".csv" onChange={handleFileChange} />
            <select value={selectedBankAccountId} onChange={(event) => setSelectedBankAccountId(event.target.value)}>
              <option value="">Aucun (non rattaché)</option>
              {bankAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
            <select value={importMode} onChange={(event) => setImportMode(event.target.value as 'analyze' | 'commit')}>
              <option value="analyze">Analyser</option>
              <option value="commit">Commit</option>
            </select>
            <select value={modifiedAction} onChange={(event) => setModifiedAction(event.target.value as 'keep' | 'replace')}>
              <option value="replace">Remplacer (recommandé)</option>
              <option value="keep">Conserver l&apos;existant</option>
            </select>
          </div>
          <p className="placeholder-text">Replace recommandé pour les lignes modifiées.</p>
          <div className="import-actions">
            <button type="button" disabled={!canImport} onClick={() => runImport('analyze', modifiedAction)}>
              {isImporting && importMode === 'analyze' ? 'Analyse...' : 'Analyser'}
            </button>
            <button type="button" disabled={!canImport} onClick={() => runImport('commit', 'replace')}>
              {isImporting && importMode === 'commit' ? 'Import...' : 'Importer (replace)'}
            </button>
          </div>
          {importError ? <p className="error-text">{importError}</p> : null}

          {importResult ? (
            <div className="import-result">
              <p>
                new: {importResult.new_count} | modified: {importResult.modified_count} | identical: {importResult.identical_count}{' '}
                | duplicates: {importResult.duplicates_count} | replaced: {importResult.replaced_count} | failed:{' '}
                {importResult.failed_count}
              </p>
              {importResult.requires_confirmation ? <p className="placeholder-text">Confirmation requise avant commit.</p> : null}

              {importResult.errors?.length ? (
                <ul>
                  {importResult.errors.slice(0, 5).map((row, index) => (
                    <li key={`${row.file}-${index}`}>
                      {row.file} {row.row_index ? `(ligne ${row.row_index})` : ''}: {row.message}
                    </li>
                  ))}
                </ul>
              ) : null}

              {importResult.preview?.length ? (
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Montant</th>
                      <th>Devise</th>
                      <th>Libellé / Payee</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importResult.preview.slice(0, 20).map((item, index) => (
                      <tr key={`${item.date}-${index}`}>
                        <td>{item.date}</td>
                        <td>{String(item.montant)}</td>
                        <td>{item.devise}</td>
                        <td>{item.libelle ?? item.payee ?? '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
          ) : null}
        </section>

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
