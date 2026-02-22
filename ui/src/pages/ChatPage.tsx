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
  toQuickReplyYesNoUiAction,
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

type ProgressUiAction = {
  type: 'ui_action'
  action: 'progress'
  percent: number
  step_label: string
  steps: string[]
}

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

const LINK_LINE_REGEX = /\[Ouvrir le PDF\]\(|https?:\/\//i

function splitAssistantReply(reply: string): string[] {
  const chunks = reply
    .split(/\n\n+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)

  return chunks.flatMap((chunk) => {
    if (chunk.length <= 350 || LINK_LINE_REGEX.test(chunk)) {
      return [chunk]
    }

    const sentenceChunks = chunk.match(/[^.!?]+[.!?]?/g) ?? [chunk]
    const compactSentences = sentenceChunks.map((sentence) => sentence.trim()).filter((sentence) => sentence.length > 0)
    if (compactSentences.length <= 1) {
      return [chunk]
    }

    const reduced: string[] = []
    let current = ''

    for (const sentence of compactSentences) {
      const candidate = current ? `${current} ${sentence}` : sentence
      if (candidate.length <= 350) {
        current = candidate
        continue
      }
      if (current) {
        reduced.push(current)
      }
      current = sentence
    }

    if (current) {
      reduced.push(current)
    }

    return reduced.length > 0 ? reduced : [chunk]
  })
}

function resolveHref(href: string, apiBaseUrl: string): string {
  if (/^\/finance\/(reports\/)?/i.test(href)) {
    return `${apiBaseUrl}${href}`
  }
  return href
}

function renderContentWithLinks(content: string, apiBaseUrl: string): ReactNode[] {
  const linksRegex = /(\[([^\]]+)\]\(([^)]+)\)|https?:\/\/[^\s]+)/g
  const matches = Array.from(content.matchAll(linksRegex))
  if (matches.length === 0) {
    return [content]
  }

  const nodes: ReactNode[] = []
  let cursor = 0
  matches.forEach((match, index) => {
    const [raw, markdownLink, markdownLabel, markdownHref] = match
    const start = match.index ?? 0
    if (start > cursor) {
      nodes.push(content.slice(cursor, start))
    }

    if (markdownLink && markdownLabel && markdownHref) {
      const href = resolveHref(markdownHref, apiBaseUrl)
      nodes.push(
        <a key={`md-link-${index}-${href}`} href={href} target="_blank" rel="noreferrer" className="inline-link">
          {markdownLabel}
        </a>,
      )
    } else {
      nodes.push(
        <a key={`raw-link-${index}-${raw}`} href={raw} target="_blank" rel="noreferrer" className="inline-link">
          {raw}
        </a>,
      )
    }

    cursor = start + raw.length
  })

  if (cursor < content.length) {
    nodes.push(content.slice(cursor))
  }

  return nodes
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

function toProgressUiAction(toolResult: ChatMessage['toolResult']): ProgressUiAction | null {
  if (!toolResult || typeof toolResult !== 'object') {
    return null
  }

  const raw = toolResult as Record<string, unknown>
  if (raw.type !== 'ui_action' || raw.action !== 'progress') {
    return null
  }

  const percent = raw.percent
  const stepLabel = raw.step_label
  const steps = raw.steps
  if (typeof percent !== 'number' || typeof stepLabel !== 'string' || !Array.isArray(steps) || !steps.every((step) => typeof step === 'string')) {
    return null
  }

  return {
    type: 'ui_action',
    action: 'progress',
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    step_label: stepLabel,
    steps,
  }
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
  const [typingCursor, setTypingCursor] = useState(0)
  const envDebugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const apiBaseUrl = useMemo(() => (import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, ''), [])
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const executedPdfMessageIdsRef = useRef<Set<string>>(new Set())
  const revealedMessageIdsRef = useRef<Set<string>>(new Set())
  const assistantQueueRef = useRef<
    Array<{
      id: string
      content: string
      toolResult: Record<string, unknown> | null
      plan: Record<string, unknown> | null
      debugPayload: unknown
    }>
  >([])
  const isDrainingAssistantQueueRef = useRef(false)
  const shouldAutoScrollRef = useRef(true)
  const previousIntentMessageIdRef = useRef<string | null>(null)
  const uploadMessageGuardsRef = useRef<Set<string>>(new Set())

  const pendingImportIntent = useMemo(() => findPendingImportIntent(messages), [messages])
  const isImportRequired = pendingImportIntent !== null
  const latestAssistantMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === 'assistant') {
        return messages[index]
      }
    }
    return null
  }, [messages])
  const quickReplyAction = useMemo(() => toQuickReplyYesNoUiAction(latestAssistantMessage?.toolResult), [latestAssistantMessage])
  const activeTypingMessageId = useMemo(() => {
    const revealed = revealedMessageIdsRef.current
    for (const item of messages) {
      if (item.role !== 'assistant') {
        continue
      }
      if (!revealed?.has(item.id)) {
        return item.id
      }
    }
    return null
  }, [messages, typingCursor])
  const shouldShowQuickReplies = Boolean(
    quickReplyAction
      && latestAssistantMessage
      && revealedMessageIdsRef.current.has(latestAssistantMessage.id)
      && activeTypingMessageId === null,
  )
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

  const scrollToBottom = () => {
    const element = messagesRef.current
    if (!element) {
      return
    }
    element.scrollTop = element.scrollHeight
  }

  useEffect(() => {
    if (shouldAutoScrollRef.current) {
      scrollToBottom()
    }
  }, [messages, typingCursor])

  useEffect(() => {
    if (!pendingImportIntent) {
      previousIntentMessageIdRef.current = null
      return
    }

    previousIntentMessageIdRef.current = pendingImportIntent.messageId
  }, [pendingImportIntent])

  function computeAssistantSegmentDelay(segment: string): number {
    return 250 + Math.min(900, segment.length * 8)
  }

  async function drainAssistantQueue() {
    if (isDrainingAssistantQueueRef.current) {
      return
    }

    isDrainingAssistantQueueRef.current = true
    try {
      while (assistantQueueRef.current.length > 0) {
        const queued = assistantQueueRef.current.shift()
        if (!queued) {
          continue
        }

        setMessages((previous) => [
          ...previous,
          {
            id: queued.id,
            role: 'assistant' as const,
            content: queued.content,
            createdAt: Date.now(),
            toolResult: queued.toolResult,
            plan: queued.plan,
            debugPayload: queued.debugPayload,
          },
        ])

        if (assistantQueueRef.current.length > 0) {
          await new Promise<void>((resolve) => {
            window.setTimeout(resolve, computeAssistantSegmentDelay(queued.content))
          })
        }
      }
    } finally {
      isDrainingAssistantQueueRef.current = false
    }
  }

  function enqueueAssistantMessages(
    segments: string[],
    toolResult: Record<string, unknown> | null,
    plan: Record<string, unknown> | null,
    debugPayload: unknown,
  ) {
    segments.forEach((segment, index) => {
      const isLast = index === segments.length - 1
      assistantQueueRef.current.push({
        id: crypto.randomUUID(),
        content: segment,
        toolResult: isLast ? toolResult : null,
        plan: isLast ? plan : null,
        debugPayload: isLast ? debugPayload : null,
      })
    })

    void drainAssistantQueue()
  }

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

  async function submitQuickReply(displayMessage: '✅' | '❌', apiMessage: 'oui' | 'non') {
    if (isLoading || isImportRequired) {
      return
    }

    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user' as const, content: displayMessage, createdAt: Date.now() }])
    setMessage('')
    setError(null)
    setIsLoading(true)

    try {
      const response = await sendChatMessage(apiMessage, { debug: debugMode })
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedMessage = message.trim()
    if (!trimmedMessage || isLoading || isImportRequired) {
      return
    }

    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user' as const, content: trimmedMessage, createdAt: Date.now() }])
    setMessage('')
    setError(null)
    setIsLoading(true)

    try {
      const response = await sendChatMessage(trimmedMessage, { debug: debugMode })
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
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
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }

  function computeProgressStepLabel(percent: number, steps: string[]): string {
    if (percent < 20) return steps[0] ?? 'Téléversement'
    if (percent < 35) return steps[1] ?? 'Détection de la banque'
    if (percent < 60) return steps[2] ?? 'Extraction des transactions'
    if (percent < 80) return steps[3] ?? 'Import en base'
    return steps[4] ?? 'Finalisation'
  }

  function buildProgressToolResult(percent: number, steps: string[]): ProgressUiAction {
    return {
      type: 'ui_action',
      action: 'progress',
      percent,
      step_label: computeProgressStepLabel(percent, steps),
      steps,
    }
  }

  function updateProgressMessage(progressId: string, progressToolResult: ProgressUiAction, content = 'Import en cours…') {
    setMessages((previous) => previous.map((item) => (
      item.id === progressId
        ? { ...item, content, toolResult: progressToolResult }
        : item
    )))
  }

  function replaceProgressWithAssistantMessage(progressId: string, assistantMessage: string, debugPayload?: unknown) {
    setMessages((previous) => previous.map((item) => (
      item.id === progressId
        ? { ...item, content: assistantMessage, toolResult: null, debugPayload: debugPayload ?? null }
        : item
    )))
  }

  function onConfirmImport(file: File, intent: ImportIntent | null) {
    const acceptedTypes = intent?.acceptedTypes ?? ['csv', 'pdf']
    const uploadFingerprint = `${file.name}-${file.size}-${file.lastModified}`
    if (!uploadMessageGuardsRef.current.has(uploadFingerprint)) {
      uploadMessageGuardsRef.current.add(uploadFingerprint)
      setMessages((previous) => [
        ...previous,
        {
          id: `upload-${uploadFingerprint}`,
          role: 'user' as const,
          content: `Fichier "${file.name}" envoyé.`,
          createdAt: Date.now(),
        },
      ])
    }

    const progressId = crypto.randomUUID()
    const steps = ['Téléversement', 'Détection de la banque', 'Extraction des transactions', 'Import en base', 'Finalisation']
    setMessages((previous) => [
      ...previous,
      {
        id: progressId,
        role: 'assistant' as const,
        content: 'Import en cours…',
        createdAt: Date.now(),
        toolResult: buildProgressToolResult(5, steps),
      },
    ])

    let currentPercent = 5
    const progressInterval = window.setInterval(() => {
      currentPercent = Math.min(85, currentPercent + 3)
      updateProgressMessage(progressId, buildProgressToolResult(currentPercent, steps))
      if (currentPercent >= 85) {
        window.clearInterval(progressInterval)
      }
    }, 250)

    setError(null)
    void (async () => {
      try {
        const contentBase64 = await readFileAsBase64(file)
        const result = await importReleves({
          files: [{ filename: file.name, content_base64: contentBase64 }],
          import_mode: 'commit',
          modified_action: 'replace',
        })

        if (isImportClarificationResult(result)) {
          window.clearInterval(progressInterval)
          replaceProgressWithAssistantMessage(progressId, result.message)
          setToast({ type: 'success', message: 'Choix du compte requis.' })
          return
        }

        window.clearInterval(progressInterval)
        updateProgressMessage(progressId, { ...buildProgressToolResult(100, steps), step_label: 'Terminé' })
        replaceProgressWithAssistantMessage(
          progressId,
          buildImportSuccessText(result, {
            messageId: intent?.messageId ?? crypto.randomUUID(),
            acceptedTypes,
            source: intent?.source ?? 'ui_request',
          }),
          result,
        )

        if (intent?.messageId) {
          setMessages((previous) => previous.map((item) => (item.id === intent.messageId ? { ...item, toolResult: null } : item)))
        }

        setToast({ type: 'success', message: 'Import terminé. Analyse automatique en cours…' })
        setIsLoading(true)
        const response = await sendChatMessage('', { debug: debugMode })
        const segments = splitAssistantReply(response.reply)
        enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
      } catch (caughtError) {
        window.clearInterval(progressInterval)
        setToast({ type: 'error', message: caughtError instanceof Error ? caughtError.message : 'Erreur inconnue pendant l’import' })
      } finally {
        setIsLoading(false)
      }
    })()
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
          apiBaseUrl={apiBaseUrl}
          typingCursor={typingCursor}
          revealedMessageIdsRef={revealedMessageIdsRef}
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
          onTypingDone={(_messageId) => setTypingCursor((value) => value + 1)}
          onTypingProgress={() => {
            if (shouldAutoScrollRef.current) {
              scrollToBottom()
            }
          }}
        />

        <QuickReplyBar
          quickReplyAction={shouldShowQuickReplies ? quickReplyAction : null}
          isLoading={isLoading}
          disabled={isImportRequired}
          onSubmitQuickReply={(option) => {
            const normalizedValue = option.value.trim().toLowerCase()
            const displayMessage = option.label.trim() === '❌' || normalizedValue === 'non' ? '❌' : '✅'
            const apiMessage: 'oui' | 'non' = normalizedValue === 'non' ? 'non' : 'oui'
            void submitQuickReply(displayMessage, apiMessage)
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
        onConfirmImport={onConfirmImport}
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
  apiBaseUrl: string
  typingCursor: number
  revealedMessageIdsRef: RefObject<Set<string>>
  messagesRef: RefObject<HTMLDivElement | null>
  onImportNow: (intent: ImportIntent) => void
  onScroll: (event: UIEvent<HTMLDivElement>) => void
  onStartConversation: () => void
  onTypingDone: (messageId: string) => void
  onActiveTypingChange?: (messageId: string | null) => void
  onTypingProgress?: () => void
}

export function MessageList({ messages, isLoading, debugMode, apiBaseUrl, typingCursor, revealedMessageIdsRef, messagesRef, onImportNow, onScroll, onStartConversation, onTypingDone, onActiveTypingChange, onTypingProgress }: MessageListProps) {
  const activeTypingMessageId = useMemo(() => {
    const revealed = revealedMessageIdsRef.current
    for (const item of messages) {
      if (item.role !== 'assistant') {
        continue
      }
      if (!revealed?.has(item.id)) {
        return item.id
      }
    }
    return null
  }, [messages, revealedMessageIdsRef, typingCursor])

  useEffect(() => {
    onActiveTypingChange?.(activeTypingMessageId)
  }, [activeTypingMessageId, onActiveTypingChange])

  const isMessageRevealed = (id: string): boolean => revealedMessageIdsRef.current?.has(id) ?? false

  return (
    <div className="messages card" aria-live="polite" ref={messagesRef} onScroll={onScroll}>
      {messages.length === 0 ? <EmptyState onStartConversation={onStartConversation} /> : null}
      {messages.map((chatMessage) => {
        if (chatMessage.role === 'assistant' && !isMessageRevealed(chatMessage.id) && chatMessage.id !== activeTypingMessageId) {
          return null
        }

        return (
        <MessageBubble
          key={chatMessage.id}
          message={chatMessage}
          debugMode={debugMode}
          onImportNow={onImportNow}
          apiBaseUrl={apiBaseUrl}
          revealedMessageIdsRef={revealedMessageIdsRef}
          isActiveTyping={chatMessage.id === activeTypingMessageId}
          onTypingDone={onTypingDone}
          onTypingProgress={onTypingProgress}
        />
        )
      })}
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

function shouldBypassTypingInTests(): boolean {
  if (import.meta.env.MODE !== 'test') {
    return false
  }

  return !(globalThis as { __CHAT_ENABLE_TYPING_IN_TESTS__?: boolean }).__CHAT_ENABLE_TYPING_IN_TESTS__
}

export function TypingText({
  message,
  apiBaseUrl,
  revealedMessageIdsRef,
  isActiveTyping,
  onTypingDone,
  onTypingProgress,
}: {
  message: ChatMessage
  apiBaseUrl: string
  revealedMessageIdsRef: RefObject<Set<string>>
  isActiveTyping: boolean
  onTypingDone: (messageId: string) => void
  onTypingProgress?: () => void
}) {
  const shouldBypassTyping = shouldBypassTypingInTests()
  const completionNotifiedRef = useRef(false)
  const lastTypingProgressAtRef = useRef(0)
  const [visibleLength, setVisibleLength] = useState(() => {
    const revealed = revealedMessageIdsRef.current
    return revealed?.has(message.id) ? message.content.length : 0
  })

  useEffect(() => {
    completionNotifiedRef.current = false
  }, [message.id])

  useEffect(() => {
    const revealed = revealedMessageIdsRef.current

    const notifyCompletion = () => {
      if (completionNotifiedRef.current) {
        return
      }
      completionNotifiedRef.current = true
      onTypingDone(message.id)
    }

    if (shouldBypassTyping) {
      setVisibleLength(message.content.length)
      if (!revealed?.has(message.id)) {
        revealed?.add(message.id)
        notifyCompletion()
      }
      return
    }

    if (revealed?.has(message.id)) {
      setVisibleLength(message.content.length)
      return
    }

    if (!isActiveTyping) {
      setVisibleLength(0)
      return
    }

    let active = true
    let timerId: number | undefined

    const step = () => {
      if (!active) {
        return
      }

      setVisibleLength((previous) => {
        const increment = previous > 120 ? 4 : 2
        const next = Math.min(message.content.length, previous + increment)
        if (next >= message.content.length) {
          if (!revealed?.has(message.id)) {
            revealed?.add(message.id)
          }
          return message.content.length
        }

        const now = Date.now()
        if (next > previous && now - lastTypingProgressAtRef.current >= 100) {
          lastTypingProgressAtRef.current = now
          onTypingProgress?.()
        }

        const delay = previous > 120 ? 14 : 20
        timerId = window.setTimeout(step, delay)
        return next
      })
    }

    timerId = window.setTimeout(step, 10)

    return () => {
      active = false
      if (timerId !== undefined) {
        window.clearTimeout(timerId)
      }
    }
  }, [isActiveTyping, onTypingProgress, shouldBypassTyping, message.id, message.content, onTypingDone, revealedMessageIdsRef])


  useEffect(() => {
    const revealed = revealedMessageIdsRef.current
    const isFullyVisible = visibleLength >= message.content.length
    if (!isFullyVisible) {
      return
    }
    if (!revealed?.has(message.id)) {
      return
    }
    if (completionNotifiedRef.current) {
      return
    }
    completionNotifiedRef.current = true
    onTypingDone(message.id)
  }, [message.id, message.content.length, onTypingDone, revealedMessageIdsRef, visibleLength])

  const content = shouldBypassTyping ? message.content : message.content.slice(0, visibleLength)

  return <>{renderContentWithLinks(content, apiBaseUrl)}</>
}

function MessageBubble({
  message,
  debugMode,
  onImportNow,
  apiBaseUrl,
  revealedMessageIdsRef,
  isActiveTyping,
  onTypingDone,
  onTypingProgress,
}: {
  message: ChatMessage
  debugMode: boolean
  onImportNow: (intent: ImportIntent) => void
  apiBaseUrl: string
  revealedMessageIdsRef: RefObject<Set<string>>
  isActiveTyping: boolean
  onTypingDone: (messageId: string) => void
  onTypingProgress?: () => void
}) {
  const dateLabel = new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const pdfUiRequest = toPdfUiRequest(message.toolResult)
  const hasPdfAction = pdfUiRequest !== null
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

  const progressUiAction = message.role === 'assistant' ? toProgressUiAction(message.toolResult) : null
  const activeStepIndex = progressUiAction
    ? Math.max(0, progressUiAction.steps.findIndex((step) => step === progressUiAction.step_label))
    : -1
  const displayedStepIndex = progressUiAction
    ? progressUiAction.step_label === 'Terminé'
      ? progressUiAction.steps.length
      : activeStepIndex + 1
    : 0

  return (
    <article className={`message message-${message.role}`}>
      <p className="message-role">{roleLabel(message.role)}</p>
      <p className="message-content">
        {message.role === 'assistant' ? (
          <TypingText
            message={message}
            apiBaseUrl={apiBaseUrl}
            revealedMessageIdsRef={revealedMessageIdsRef}
            isActiveTyping={isActiveTyping}
            onTypingDone={onTypingDone}
            onTypingProgress={onTypingProgress}
          />
        ) : (
          renderContentWithLinks(message.content, apiBaseUrl)
        )}
      </p>
      {message.role === 'assistant' && progressUiAction ? (
        <div style={{ marginTop: '0.5rem' }} aria-label="import-progress">
          <div style={{ height: '8px', borderRadius: '999px', background: 'rgba(255,255,255,0.12)', overflow: 'hidden' }}>
            <div style={{ height: '8px', borderRadius: '999px', background: 'rgba(255,255,255,0.6)', width: `${progressUiAction.percent}%`, transition: 'width 200ms ease' }} />
          </div>
          <p className="subtle-text" style={{ marginTop: '0.35rem' }}>
            Étape: {displayedStepIndex} / {progressUiAction.steps.length} — {progressUiAction.step_label}
          </p>
          <ul className="subtle-text" style={{ margin: '0.25rem 0 0', paddingLeft: '1.1rem' }}>
            {progressUiAction.steps.map((step, index) => (
              <li key={`${step}-${index}`} style={{ fontWeight: index === activeStepIndex ? 700 : 400 }}>
                {step}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="message-meta-row">
        <span className="subtle-text">{dateLabel}</span>
        {hasPdfAction ? (
          <span
            className="pdf-pill"
            role="button"
            tabIndex={0}
            style={{ cursor: 'pointer' }}
            onClick={() => {
              if (pdfUiRequest) {
                void openPdfFromUrl(pdfUiRequest.url)
              }
            }}
            onKeyDown={(event) => {
              if ((event.key === 'Enter' || event.key === ' ') && pdfUiRequest) {
                event.preventDefault()
                void openPdfFromUrl(pdfUiRequest.url)
              }
            }}
          >
            PDF
          </span>
        ) : null}
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


function QuickReplyBar({
  quickReplyAction,
  isLoading,
  disabled,
  onSubmitQuickReply,
}: {
  quickReplyAction: ReturnType<typeof toQuickReplyYesNoUiAction>
  isLoading: boolean
  disabled: boolean
  onSubmitQuickReply: (option: { id: string; label: string; value: string }) => void
}) {
  if (!quickReplyAction) {
    return null
  }

  return (
    <div className="message-actions" aria-label="Quick reply yes no">
      {quickReplyAction.options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="secondary-button"
          onClick={() => onSubmitQuickReply(option)}
          disabled={isLoading || disabled}
          aria-label={`Quick reply ${option.value}`}
        >
          {option.label}
        </button>
      ))}
    </div>
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
  onConfirmImport: (file: File, intent: ImportIntent | null) => void
  onImportError: (messageText: string) => void
}

function ImportDialog({
  isOpen,
  autoOpenPicker,
  onAutoOpenHandled,
  onClose,
  pendingImportIntent,
  onConfirmImport,
  onImportError,
}: ImportDialogProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const acceptedTypes = pendingImportIntent?.acceptedTypes ?? ['csv', 'pdf']
  const accept = acceptedTypes.map((type) => `.${type.replace(/^\./, '')}`).join(',')

  useEffect(() => {
    if (autoOpenPicker && isOpen) {
      inputRef.current?.click()
      onAutoOpenHandled()
    }
  }, [autoOpenPicker, isOpen, onAutoOpenHandled])

  if (!isOpen) {
    return null
  }

  function handleImport() {
    if (!selectedFile) {
      return
    }

    const extension = selectedFile.name.split('.').pop()?.toLowerCase()
    if (!extension || !acceptedTypes.includes(extension)) {
      onImportError('Format invalide. Sélectionne un fichier compatible.')
      return
    }

    onConfirmImport(selectedFile, pendingImportIntent)
    setSelectedFile(null)
    onClose()
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
          <input id="import-file-input" ref={inputRef} type="file" accept={accept} onChange={handleFileChange} />
          <span>{selectedFile ? `Fichier: ${selectedFile.name}` : 'Dépose le fichier ici ou clique pour le choisir'}</span>
          <small>{selectedFile ? formatFileSize(selectedFile.size) : `Formats acceptés: ${acceptedTypes.join(', ')}`}</small>
        </label>

        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Annuler
          </button>
          <button type="button" onClick={handleImport} disabled={!selectedFile}>
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
