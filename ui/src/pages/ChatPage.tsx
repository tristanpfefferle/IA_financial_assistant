import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent, type ReactNode, type RefObject, type UIEvent } from 'react'

import {
  getPendingMerchantAliasesCount,
  hardResetProfile,
  importReleves,
  isImportClarificationResult,
  openPdfFromUrl,
  resolvePendingMerchantAliases,
  resetSession,
  sendChatMessage,
  type RelevesImportResult,
} from '../api/agentApi'
import { DebugPanel } from '../components/DebugPanel'
import { installSessionResetOnPageExit, logoutWithSessionReset } from '../lib/sessionLifecycle'
import { supabase } from '../lib/supabaseClient'
import {
  claimPdfUiRequestExecution,
  toLegacyImportUiRequest,
  toOpenImportPanelUiAction,
  toPdfUiRequest,
} from './chatUiRequests'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  toolResult?: Record<string, unknown> | null
  plan?: Record<string, unknown> | null
  debugPayload?: unknown
}

type ChatPageProps = {
  email?: string
}

type ImportIntent = {
  messageId: string
  acceptedTypes: string[]
  source: 'ui_action' | 'ui_request'
}

type ToastState = { type: 'error' | 'success'; message: string } | null

function formatFileSize(fileSize: number): string {
  if (fileSize < 1024) {
    return `${fileSize} o`
  }
  if (fileSize < 1024 * 1024) {
    return `${(fileSize / 1024).toFixed(1)} Ko`
  }
  return `${(fileSize / (1024 * 1024)).toFixed(2)} Mo`
}

function buildImportSuccessText(result: RelevesImportResult, _intent: ImportIntent): string {
  const typedResult = result as RelevesImportResult & {
    transactions_imported_count?: number
    transactions_imported?: number
    date_range?: { start: string; end: string } | null
    bank_account_name?: string | null
  }
  const importedCount = typedResult.transactions_imported_count ?? typedResult.transactions_imported ?? result.imported_count ?? 0
  const accountName = typedResult.bank_account_name ?? 'ce compte'
  const dateRange = typedResult.date_range ?? null

  if (dateRange) {
    return `Parfait, j’ai bien reçu ton relevé ${accountName}.

${importedCount} transactions détectées entre le ${dateRange.start} et le ${dateRange.end}.`
  }

  return `Parfait, j’ai bien reçu ton relevé ${accountName}.

${importedCount} transactions détectées.`
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

function roleLabel(role: ChatMessage['role']): string {
  return role === 'user' ? 'Vous' : 'Assistant'
}

function renderContentWithLinks(content: string): ReactNode[] {
  const linksRegex = /(https?:\/\/[^\s]+)/g
  const parts = content.split(linksRegex)
  return parts.map((part, index) => {
    if (linksRegex.test(part)) {
      return (
        <a key={`link-${part}-${index}`} href={part} target="_blank" rel="noreferrer" className="inline-link">
          {part}
        </a>
      )
    }
    return part
  })
}

function findPendingImportIntent(messages: ChatMessage[]): ImportIntent | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const action = toOpenImportPanelUiAction(message.toolResult)
    if (action) {
      return {
        messageId: message.id,
        acceptedTypes: action.accepted_types ?? ['csv', 'pdf'],
        source: 'ui_action',
      }
    }

    const legacyRequest = toLegacyImportUiRequest(message.toolResult)
    if (legacyRequest) {
      return {
        messageId: message.id,
        acceptedTypes: legacyRequest.accepted_types ?? ['csv', 'pdf'],
        source: 'ui_request',
      }
    }
  }

  return null
}

export function ChatPage({ email }: ChatPageProps) {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<ToastState>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [isRefreshingSession, setIsRefreshingSession] = useState(false)
  const [pendingMerchantAliasesCount, setPendingMerchantAliasesCount] = useState(0)
  const [isResolvingPendingAliases, setIsResolvingPendingAliases] = useState(false)
  const [resolvePendingAliasesFeedback, setResolvePendingAliasesFeedback] = useState<string | null>(null)
  const [debugMode, setDebugMode] = useState(false)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isImportDialogOpen, setIsImportDialogOpen] = useState(false)
  const [autoOpenImportPicker, setAutoOpenImportPicker] = useState(false)
  const envDebugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const apiBaseUrl = useMemo(() => (import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, ''), [])
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const executedPdfMessageIdsRef = useRef<Set<string>>(new Set())
  const shouldAutoScrollRef = useRef(true)
  const previousIntentMessageIdRef = useRef<string | null>(null)

  const pendingImportIntent = useMemo(() => findPendingImportIntent(messages), [messages])
  const isImportRequired = pendingImportIntent !== null
  const hasUnauthorizedError = useMemo(() => error?.includes('(401)') ?? false, [error])
  const statusBadge = debugMode ? 'Debug' : isImportRequired ? 'Onboarding' : 'Prêt'

  useEffect(() => {
    if (!toast) {
      return
    }
    const timeoutId = window.setTimeout(() => setToast(null), 3500)
    return () => window.clearTimeout(timeoutId)
  }, [toast])

  useEffect(() => {
    const storedDebugMode = localStorage.getItem('debugMode')
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
    localStorage.setItem('debugMode', debugMode ? '1' : '0')
  }, [debugMode])

  useEffect(() => {
    if (!hasToken) {
      return
    }
    return installSessionResetOnPageExit(() => {
      void resetSession({ keepalive: true, timeoutMs: 1500 })
    })
  }, [hasToken])

  useEffect(() => {
    if (!hasToken) {
      setPendingMerchantAliasesCount(0)
      return
    }

    let active = true

    getPendingMerchantAliasesCount()
      .then((result) => {
        if (active) {
          setPendingMerchantAliasesCount(Math.max(0, result.pending_total_count || 0))
        }
      })
      .catch(() => {
        if (active) {
          setPendingMerchantAliasesCount(0)
        }
      })

    return () => {
      active = false
    }
  }, [hasToken])

  useEffect(() => {
    const pendingPdfMessages = messages.filter((chatMessage) => {
      if (chatMessage.role !== 'assistant') {
        return false
      }
      if (executedPdfMessageIdsRef.current.has(chatMessage.id)) {
        return false
      }
      return toPdfUiRequest(chatMessage.toolResult) !== null
    })

    for (const chatMessage of pendingPdfMessages) {
      const pdfUiRequest = claimPdfUiRequestExecution(
        executedPdfMessageIdsRef.current,
        chatMessage.id,
        chatMessage.toolResult,
      )
      if (!pdfUiRequest) {
        continue
      }
      openPdfFromUrl(pdfUiRequest.url).catch((caughtError) => {
        setError(caughtError instanceof Error ? caughtError.message : 'Impossible d’ouvrir le rapport PDF')
      })
    }
  }, [messages])

  useEffect(() => {
    const messageContainer = messagesRef.current
    if (!messageContainer || !shouldAutoScrollRef.current) {
      return
    }
    messageContainer.scrollTop = messageContainer.scrollHeight
  }, [messages, isLoading])

  useEffect(() => {
    if (!pendingImportIntent) {
      previousIntentMessageIdRef.current = null
      return
    }

    previousIntentMessageIdRef.current = pendingImportIntent.messageId
  }, [pendingImportIntent])

  async function handleLogout() {
    setError(null)
    await logoutWithSessionReset({
      resetSession: () => resetSession({ timeoutMs: 1500 }),
      signOut: () => supabase.auth.signOut(),
      onLogoutError: () => setError('Impossible de vous déconnecter pour le moment. Veuillez réessayer.'),
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

  async function handleResolvePendingAliases() {
    if (isResolvingPendingAliases) {
      return
    }

    setResolvePendingAliasesFeedback(null)
    setIsResolvingPendingAliases(true)
    setError(null)
    try {
      const result = await resolvePendingMerchantAliases({ limit: 20, max_batches: 10 })
      const applied = Number(result.stats.applied ?? 0)
      const failed = Number(result.stats.failed ?? 0)
      const pendingAfter = result.pending_after ?? 0
      setPendingMerchantAliasesCount(Math.max(0, pendingAfter))
      setResolvePendingAliasesFeedback(`Résolution terminée: ${applied} appliqués, ${failed} failed, pending_after=${pendingAfter}.`)
    } catch (caughtError) {
      setResolvePendingAliasesFeedback(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsResolvingPendingAliases(false)
    }
  }

  async function handleHardReset() {
    if (!window.confirm('Confirmer le reset complet des données de votre profil de test ?')) return
    if (!window.confirm('Dernière confirmation: cette action est irréversible. Continuer ?')) return

    setError(null)
    try {
      await hardResetProfile()
      setMessages([])
      await resetSession({ timeoutMs: 1500 })
      window.location.reload()
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      if (caughtError instanceof Error && (errorMessage.includes('(404)') || errorMessage.includes('Not found'))) {
        setError('Endpoint de debug désactivé (DEBUG_ENDPOINTS_ENABLED=true requis côté backend).')
        return
      }
      setError(errorMessage)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedMessage = message.trim()
    if (!trimmedMessage || isLoading || isImportRequired) {
      return
    }

    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user', content: trimmedMessage, createdAt: Date.now() }])
    setMessage('')
    setError(null)
    setIsLoading(true)

    try {
      const response = await sendChatMessage(trimmedMessage, { debug: debugMode })
      setMessages((previous) => [
        ...previous,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
          plan: response.plan,
          debugPayload: response,
        },
      ])
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }

  async function startConversation() {
    if (isLoading) {
      return
    }

    setIsLoading(true)
    setError(null)
    try {
      const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
      setMessages((previous) => [
        ...previous,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
          plan: response.plan,
          debugPayload: response,
        },
      ])
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="chat-layout">
      <button type="button" className="mobile-menu-button secondary-button" onClick={() => setIsSidebarOpen((open) => !open)}>
        Menu
      </button>

      <Sidebar
        isOpen={isSidebarOpen}
        statusBadge={statusBadge}
        debugMode={debugMode}
        setDebugMode={setDebugMode}
        pendingMerchantAliasesCount={pendingMerchantAliasesCount}
        isResolvingPendingAliases={isResolvingPendingAliases}
        onResolvePendingAliases={handleResolvePendingAliases}
        resolvePendingAliasesFeedback={resolvePendingAliasesFeedback}
        onHardReset={handleHardReset}
        envDebugEnabled={envDebugEnabled}
        apiBaseUrl={apiBaseUrl}
        email={email}
        hasToken={hasToken}
      />

      <section className="chat-panel">
        <ChatHeader onLogout={handleLogout} />

        <MessageList
          messages={messages}
          isLoading={isLoading}
          debugMode={debugMode}
          messagesRef={messagesRef}
          onImportNow={(intent) => {
            setIsImportDialogOpen(true)
            setAutoOpenImportPicker(true)
            if (intent.messageId !== pendingImportIntent?.messageId) {
              setMessages((previous) =>
                previous.map((item) =>
                  item.id === intent.messageId
                    ? {
                        ...item,
                        toolResult:
                          intent.source === 'ui_action'
                            ? {
                                type: 'ui_action',
                                action: 'open_import_panel',
                                accepted_types: intent.acceptedTypes,
                              }
                            : {
                                type: 'ui_request',
                                name: 'import_file',
                                accepted_types: intent.acceptedTypes,
                              },
                      }
                    : item,
                ),
              )
            }
          }}
          onScroll={(event) => {
            const element = event.currentTarget
            const threshold = 48
            shouldAutoScrollRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < threshold
          }}
          onStartConversation={() => {
            void startConversation()
          }}
          />

        <Composer
          message={message}
          setMessage={setMessage}
          onSubmit={handleSubmit}
          isLoading={isLoading}
          disabled={isImportRequired}
        />

        {error ? <p className="error-text">{error}</p> : null}
        {hasUnauthorizedError && email ? (
          <div className="session-recovery">
            <button type="button" className="secondary-button" onClick={handleRefreshSession} disabled={isRefreshingSession}>
              {isRefreshingSession ? 'Rafraîchissement...' : 'Rafraîchir la session'}
            </button>
            <p className="subtle-text">Si le problème persiste, reconnectez-vous pour renouveler vos identifiants.</p>
          </div>
        ) : null}
      </section>

      <ImportDialog
        isOpen={isImportDialogOpen}
        autoOpenPicker={autoOpenImportPicker}
        onAutoOpenHandled={() => setAutoOpenImportPicker(false)}
        onClose={() => setIsImportDialogOpen(false)}
        pendingImportIntent={pendingImportIntent}
        onImportSuccess={(resultMessage, debugPayload, sourceMessageId) => {
          setMessages((previous) => {
            if (!sourceMessageId) {
              return [...previous, { id: crypto.randomUUID(), role: 'assistant', content: resultMessage, createdAt: Date.now(), debugPayload }]
            }

            const index = previous.findIndex((item) => item.id === sourceMessageId)
            if (index < 0) {
              return [...previous, { id: crypto.randomUUID(), role: 'assistant', content: resultMessage, createdAt: Date.now(), debugPayload }]
            }

            const updated = [...previous]
            updated[index] = { ...updated[index], toolResult: null }
            updated.splice(index + 1, 0, {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: resultMessage,
              createdAt: Date.now(),
              debugPayload,
            })
            return updated
          })
          setIsImportDialogOpen(false)
          setToast({ type: 'success', message: 'Import terminé. Analyse automatique en cours…' })

          setIsLoading(true)
          setError(null)
          void sendChatMessage('', { debug: debugMode }).then((response) => {
            setMessages((previous) => [
              ...previous,
              {
                id: crypto.randomUUID(),
                role: 'assistant',
                content: response.reply,
                createdAt: Date.now(),
                toolResult: response.tool_result,
                plan: response.plan,
                debugPayload: response,
              },
            ])
          }).catch((caughtError) => {
            setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
          }).finally(() => {
            setIsLoading(false)
          })
        }}
        onImportClarification={(assistantMessage) => {
          setMessages((previous) => [
            ...previous,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: assistantMessage,
              createdAt: Date.now(),
            },
          ])
          setToast({ type: 'success', message: 'Choix du compte requis.' })
        }}
        onImportError={(messageText) => setToast({ type: 'error', message: messageText })}
      />

      <Toast toast={toast} />
    </main>
  )
}

type SidebarProps = {
  isOpen: boolean
  statusBadge: string
  debugMode: boolean
  setDebugMode: (value: boolean) => void
  pendingMerchantAliasesCount: number
  isResolvingPendingAliases: boolean
  onResolvePendingAliases: () => void
  resolvePendingAliasesFeedback: string | null
  onHardReset: () => void
  envDebugEnabled: boolean
  apiBaseUrl: string
  email?: string
  hasToken: boolean
}

function Sidebar(props: SidebarProps) {
  return (
    <aside className={`sidebar ${props.isOpen ? 'open' : ''}`}>
      <section className="card sidebar-card">
        <h2>Profil & Actions</h2>
        <p className="status-badge">{props.statusBadge}</p>
        <label className="switch-row">
          <input type="checkbox" checked={props.debugMode} onChange={(event) => props.setDebugMode(event.target.checked)} />
          Mode debug
        </label>

        {props.pendingMerchantAliasesCount > 0 ? (
          <button type="button" className="secondary-button" onClick={props.onResolvePendingAliases} disabled={props.isResolvingPendingAliases}>
            {props.isResolvingPendingAliases ? 'Résolution en cours…' : 'Résoudre les marchands restants'}
          </button>
        ) : null}

        {props.debugMode ? (
          <button type="button" className="secondary-button" onClick={props.onHardReset}>
            Reset (tests)
          </button>
        ) : null}

        {props.resolvePendingAliasesFeedback ? <p className="subtle-text">{props.resolvePendingAliasesFeedback}</p> : null}
      </section>


      {props.envDebugEnabled ? (
        <section className="card sidebar-card debug-banner" role="status" aria-live="polite">
          Connecté: {props.email ? 'oui' : 'non'} · Token: {props.hasToken ? 'présent' : 'absent'} · API: {props.apiBaseUrl}
        </section>
      ) : null}
    </aside>
  )
}

function ChatHeader({ onLogout }: { onLogout: () => void }) {
  return (
    <header className="chat-header sticky-top">
      <div>
        <h1>Assistant financier IA</h1>
        <p className="subtle-text">Analyse tes relevés, classe tes dépenses et répond à tes questions rapidement.</p>
      </div>
      <button type="button" className="secondary-button" onClick={onLogout}>
        Se déconnecter
      </button>
    </header>
  )
}

type MessageListProps = {
  messages: ChatMessage[]
  isLoading: boolean
  debugMode: boolean
  messagesRef: RefObject<HTMLDivElement | null>
  onImportNow: (intent: ImportIntent) => void
  onScroll: (event: UIEvent<HTMLDivElement>) => void
  onStartConversation: () => void
}

function MessageList({ messages, isLoading, debugMode, messagesRef, onImportNow, onScroll, onStartConversation }: MessageListProps) {
  return (
    <div className="messages card" aria-live="polite" ref={messagesRef} onScroll={onScroll}>
      {messages.length === 0 ? <EmptyState onStartConversation={onStartConversation} /> : null}
      {messages.map((chatMessage) => (
        <MessageBubble key={chatMessage.id} message={chatMessage} debugMode={debugMode} onImportNow={onImportNow} />
      ))}
      {isLoading ? (
        <div className="loading-state">
          <span className="spinner" />
          <p className="subtle-text">L’assistant réfléchit…</p>
        </div>
      ) : null}
    </div>
  )
}

function EmptyState({ onStartConversation }: { onStartConversation: () => void }) {
  return (
    <section className="empty-state">
      <h3>Bienvenue 👋</h3>
      <p className="subtle-text">Je peux t’aider à comprendre tes dépenses, tes revenus et tes tendances.</p>
      <div className="empty-actions">
        <button type="button" onClick={onStartConversation}>
          Commencer
        </button>
      </div>
    </section>
  )
}

function MessageBubble({ message, debugMode, onImportNow }: { message: ChatMessage; debugMode: boolean; onImportNow: (intent: ImportIntent) => void }) {
  const dateLabel = new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const hasPdfAction = toPdfUiRequest(message.toolResult) !== null
  const importUiAction = toOpenImportPanelUiAction(message.toolResult)
  const importUiRequest = toLegacyImportUiRequest(message.toolResult)
  const importIntent: ImportIntent | null = importUiAction
    ? {
        messageId: message.id,
        acceptedTypes: importUiAction.accepted_types ?? ['csv', 'pdf'],
        source: 'ui_action',
      }
    : importUiRequest
      ? {
          messageId: message.id,
          acceptedTypes: importUiRequest.accepted_types ?? ['csv', 'pdf'],
          source: 'ui_request',
        }
      : null

  return (
    <article className={`message message-${message.role}`}>
      <p className="message-role">{roleLabel(message.role)}</p>
      <p className="message-content">{renderContentWithLinks(message.content)}</p>
      <div className="message-meta-row">
        <span className="subtle-text">{dateLabel}</span>
        {hasPdfAction ? <span className="pdf-pill">PDF</span> : null}
      </div>
      {message.role === 'assistant' && importIntent ? (
        <div className="message-actions">
          <button type="button" className="secondary-button" onClick={() => onImportNow(importIntent)}>
            Importer maintenant
          </button>
        </div>
      ) : null}
      {debugMode && message.role === 'assistant' ? <DebugPanel payload={message.debugPayload ?? null} /> : null}
    </article>
  )
}

function Composer({
  message,
  setMessage,
  onSubmit,
  isLoading,
  disabled,
}: {
  message: string
  setMessage: (next: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  isLoading: boolean
  disabled: boolean
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (!textareaRef.current) return
    textareaRef.current.style.height = 'auto'
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 128)}px`
  }, [message])

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      const form = event.currentTarget.form
      form?.requestSubmit()
    }
  }

  return (
    <form onSubmit={onSubmit} className="composer sticky-bottom">
      <textarea
        ref={textareaRef}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={handleComposerKeyDown}
        placeholder={disabled ? 'Import requis avant de continuer.' : 'Pose une question sur tes finances…'}
        aria-label="Message"
        rows={1}
        disabled={disabled}
      />
      <button type="submit" disabled={isLoading || disabled || message.trim().length === 0}>
        Envoyer
      </button>
    </form>
  )
}

type ImportDialogProps = {
  isOpen: boolean
  autoOpenPicker: boolean
  onAutoOpenHandled: () => void
  onClose: () => void
  pendingImportIntent: ImportIntent | null
  onImportSuccess: (resultMessage: string, debugPayload: unknown, sourceMessageId?: string) => void
  onImportClarification: (assistantMessage: string) => void
  onImportError: (messageText: string) => void
}

function ImportDialog({
  isOpen,
  autoOpenPicker,
  onAutoOpenHandled,
  onClose,
  pendingImportIntent,
  onImportSuccess,
  onImportClarification,
  onImportError,
}: ImportDialogProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const [progress, setProgress] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const acceptedTypes = pendingImportIntent?.acceptedTypes ?? ['csv', 'pdf']
  const accept = acceptedTypes.map((type) => `.${type.replace(/^\./, '')}`).join(',')

  useEffect(() => {
    if (autoOpenPicker && isOpen) {
      inputRef.current?.click()
      onAutoOpenHandled()
    }
  }, [autoOpenPicker, isOpen, onAutoOpenHandled])

  useEffect(() => {
    if (!isImporting) {
      setProgress(0)
      return
    }

    const interval = window.setInterval(() => {
      setProgress((value) => (value >= 90 ? value : value + 10))
    }, 180)

    return () => window.clearInterval(interval)
  }, [isImporting])

  if (!isOpen) {
    return null
  }

  async function handleImport() {
    if (!selectedFile || isImporting) {
      return
    }

    const extension = selectedFile.name.split('.').pop()?.toLowerCase()
    if (!extension || !acceptedTypes.includes(extension)) {
      onImportError('Format invalide. Sélectionne un fichier compatible.')
      return
    }

    setIsImporting(true)
    try {
      const contentBase64 = await readFileAsBase64(selectedFile)
      const result = await importReleves({
        files: [{ filename: selectedFile.name, content_base64: contentBase64 }],
        import_mode: 'commit',
        modified_action: 'replace',
      })

      if (isImportClarificationResult(result)) {
        onClose()
        onImportClarification(result.message)
        return
      }

      setProgress(100)
      onImportSuccess(buildImportSuccessText(result, {
        messageId: pendingImportIntent?.messageId ?? crypto.randomUUID(),
        acceptedTypes,
        source: pendingImportIntent?.source ?? 'ui_request',
      }), result, pendingImportIntent?.messageId)
      setSelectedFile(null)
    } catch (caughtError) {
      onImportError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue pendant l’import')
    } finally {
      setIsImporting(false)
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file) {
      setSelectedFile(file)
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <section className="dialog card" role="dialog" aria-modal="true" aria-label="Importer un relevé" onClick={(event) => event.stopPropagation()}>
        <h3>Importer un relevé</h3>
        <p className="subtle-text">Ajoute ton fichier pour continuer l’analyse.</p>

        <label className="dropzone" htmlFor="import-file-input">
          <input id="import-file-input" ref={inputRef} type="file" accept={accept} onChange={handleFileChange} disabled={isImporting} />
          <span>{selectedFile ? `Fichier: ${selectedFile.name}` : 'Dépose le fichier ici ou clique pour le choisir'}</span>
          <small>{selectedFile ? formatFileSize(selectedFile.size) : `Formats acceptés: ${acceptedTypes.join(', ')}`}</small>
        </label>



        {isImporting ? (
          <div>
            <progress value={progress} max={100} aria-label="envoi" />
            <p className="subtle-text">Envoi…</p>
          </div>
        ) : null}

        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose} disabled={isImporting}>
            Annuler
          </button>
          <button type="button" onClick={() => void handleImport()} disabled={!selectedFile || isImporting}>
            Importer
          </button>
        </div>
      </section>
    </div>
  )
}

function Toast({ toast }: { toast: ToastState }) {
  if (!toast) {
    return null
  }

  return <div className={`toast ${toast.type === 'error' ? 'toast-error' : 'toast-success'}`}>{toast.message}</div>
}
