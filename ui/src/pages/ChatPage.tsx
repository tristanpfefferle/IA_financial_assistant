import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'

import {
  fetchPendingTransactions,
  getSpendingReport,
  getPendingMerchantAliasesCount,
  hardResetProfile,
  importReleves,
  isImportClarificationResult,
  openPdfFromUrl,
  resolveApiBaseUrl,
  resolvePendingMerchantAliases,
  resetSession,
  sendChatMessage,
  type SpendingReport,
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
  toFormUiAction,
} from './chatUiRequests'
import { resolvePdfReportUrl } from './pdfUrl'

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

type LoopDebugState = {
  loopId: string | null
  step: string | null
  blocking: boolean | null
}

type GlobalStateMode = 'onboarding' | 'free_chat' | string

const CHAT_DEBUG_STORAGE_KEY = 'chat_debug'
const CHAT_LOCAL_STORAGE_KEYS = [CHAT_DEBUG_STORAGE_KEY]

function isChatDebugEnabledByEnv(): boolean {
  return import.meta.env.VITE_CHAT_DEBUG === '1'
}

function readChatDebugMode(): boolean {
  const storedValue = localStorage.getItem(CHAT_DEBUG_STORAGE_KEY)
  if (storedValue === '1') {
    return true
  }
  if (storedValue === '0') {
    return false
  }

  return isChatDebugEnabledByEnv()
}

function toLoopDebugState(payload: unknown): LoopDebugState | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const debug = (payload as { debug?: unknown }).debug
  if (!debug || typeof debug !== 'object') {
    return null
  }

  const loop = (debug as { loop?: unknown }).loop
  if (!loop || typeof loop !== 'object') {
    return { loopId: null, step: null, blocking: null }
  }

  const rawLoop = loop as { loop_id?: unknown; step?: unknown; blocking?: unknown }
  return {
    loopId: typeof rawLoop.loop_id === 'string' ? rawLoop.loop_id : null,
    step: typeof rawLoop.step === 'string' ? rawLoop.step : null,
    blocking: typeof rawLoop.blocking === 'boolean' ? rawLoop.blocking : null,
  }
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

function buildImportErrorText(message: string): string {
  const trimmedMessage = message.trim()
  const cleanMessage = /[.!?]$/.test(trimmedMessage) ? trimmedMessage.slice(0, -1) : trimmedMessage
  return `❌ Import impossible: ${cleanMessage}. Vérifie que tu as bien exporté un CSV depuis ton e-banking.`
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

function parseSpendingReportParams(url: string): { month?: string; start_date?: string; end_date?: string } | null {
  const isSpendingPdfUrl = /\/finance\/reports\/spending\.pdf/i.test(url)
  if (!isSpendingPdfUrl) {
    return null
  }

  const resolvedUrl = url.startsWith('http://') || url.startsWith('https://') ? url : `http://local${url}`
  const searchParams = new URL(resolvedUrl).searchParams

  return {
    month: searchParams.get('month') ?? undefined,
    start_date: searchParams.get('start_date') ?? undefined,
    end_date: searchParams.get('end_date') ?? undefined,
  }
}

function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat('fr-CH', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value) + ` ${currency}`
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
        acceptedTypes: sanitizeImportAcceptedTypes(action.accepted_types),
        source: 'ui_action',
      }
    }

    const legacyRequest = toLegacyImportUiRequest(message.toolResult)
    if (legacyRequest) {
      return {
        messageId: message.id,
        acceptedTypes: sanitizeImportAcceptedTypes(legacyRequest.accepted_types),
        source: 'ui_request',
      }
    }
  }

  return null
}

function sanitizeImportAcceptedTypes(_acceptedTypes: string[] | undefined): string[] {
  // CSV-only
  return ['csv']
}

function isImportErrorResult(x: unknown): x is { ok: false; type: 'error'; message?: string; error?: { message?: string } } {
  if (!x || typeof x !== 'object') {
    return false
  }

  const candidate = x as { ok?: unknown; type?: unknown }
  return candidate.ok === false && candidate.type === 'error'
}

function buildUiFormSubmitMessage(formId: string, values: Record<string, unknown>): string {
  return `__ui_form_submit__:${JSON.stringify({ form_id: formId, values })}`
}

function buildUiFormHumanText(formId: string, values: Record<string, unknown>): string {
  if (formId === 'onboarding_profile_name') {
    const firstName = String(values.first_name ?? '').trim()
    const lastName = String(values.last_name ?? '').trim()
    return `Je m'appelle ${firstName} ${lastName}.`
  }

  if (formId === 'onboarding_profile_birth_date') {
    const birthDate = String(values.birth_date ?? '').trim()
    return `Je suis né le ${formatFrenchBirthDate(birthDate)}.`
  }

  if (formId === 'onboarding_bank_accounts') {
    const selectedBanks = Array.isArray(values.selected_banks)
      ? values.selected_banks.map((item) => String(item).trim()).filter((item) => item.length > 0)
      : []

    if (selectedBanks.length === 0) {
      return "Je valide mes banques."
    }
    if (selectedBanks.length === 1) {
      return `J'ai un compte chez ${selectedBanks[0]}.`
    }
    const head = selectedBanks.slice(0, -1).join(', ')
    return `J'ai des comptes chez ${head} et ${selectedBanks[selectedBanks.length - 1]}.`
  }

  return 'Je valide le formulaire.'
}

function formatFrenchBirthDate(iso: string): string {
  const candidate = iso.trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(candidate)) {
    return candidate
  }

  const parsedDate = new Date(`${candidate}T00:00:00Z`)
  if (Number.isNaN(parsedDate.getTime())) {
    return candidate
  }

  const formatter = new Intl.DateTimeFormat('fr-CH', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
  return formatter.format(parsedDate)
}

function buildFormSubmitPayload(formId: string, values: Record<string, unknown>): { humanText: string; messageToBackend: string } {
  const humanText = buildUiFormHumanText(formId, values)
  const structuredMessage = buildUiFormSubmitMessage(formId, values)
  return {
    humanText,
    messageToBackend: `${humanText}\n${structuredMessage}`,
  }
}

type ComposerMode = 'form' | 'quick_replies' | 'text'

function resolveGlobalStateMode(payload: unknown): GlobalStateMode | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const root = payload as { global_state?: unknown; debug?: { global_state?: unknown } }
  const nestedGlobalState = root.global_state ?? root.debug?.global_state
  if (!nestedGlobalState || typeof nestedGlobalState !== 'object') {
    return null
  }

  const mode = (nestedGlobalState as { mode?: unknown }).mode
  return typeof mode === 'string' ? mode : null
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
  const [pendingCategorizationCount, setPendingCategorizationCount] = useState(0)
  const [isResolvingPendingAliases, setIsResolvingPendingAliases] = useState(false)
  const [resolvePendingAliasesFeedback, setResolvePendingAliasesFeedback] = useState<string | null>(null)
  const [debugMode, setDebugMode] = useState(false)
  const [loopDebug, setLoopDebug] = useState<LoopDebugState | null>(null)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [globalStateMode, setGlobalStateMode] = useState<GlobalStateMode | null>(null)
  const [isImportDialogOpen, setIsImportDialogOpen] = useState(false)
  const [autoOpenImportPicker, setAutoOpenImportPicker] = useState(false)
  const [awaitingPendingCategorizationReply, setAwaitingPendingCategorizationReply] = useState(false)
  const [isOptimisticallySubmittingForm, setIsOptimisticallySubmittingForm] = useState(false)
  const [typingCursor, setTypingCursor] = useState(0)
  const envDebugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const apiBaseUrl = useMemo(() => resolveApiBaseUrl(import.meta.env.VITE_API_URL), [])
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
  const previousIntentMessageIdRef = useRef<string | null>(null)
  const uploadMessageGuardsRef = useRef<Set<string>>(new Set())
  const hasPromptedPendingCategorizationRef = useRef(false)
  const lastPromptedPendingCategorizationCountRef = useRef<number>(0)
  const didInitConversationRef = useRef(false)

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
  const formUiAction = useMemo(() => toFormUiAction(latestAssistantMessage?.toolResult), [latestAssistantMessage])
  const isGuidedMode = globalStateMode !== 'free_chat'
  const composerMode: ComposerMode = useMemo(() => {
    if (isOptimisticallySubmittingForm) {
      return 'text'
    }
    if (formUiAction) {
      return 'form'
    }
    if (quickReplyAction) {
      return 'quick_replies'
    }
    if (isGuidedMode) {
      return 'quick_replies'
    }
    return 'text'
  }, [formUiAction, isGuidedMode, isOptimisticallySubmittingForm, quickReplyAction])
  const shouldShowGuidedPlaceholder = isGuidedMode && !formUiAction && !quickReplyAction
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
    setDebugMode(readChatDebugMode())

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
    if (debugMode) {
      localStorage.setItem(CHAT_DEBUG_STORAGE_KEY, '1')
      return
    }

    localStorage.removeItem(CHAT_DEBUG_STORAGE_KEY)
  }, [debugMode])

  function syncLoopDebug(payload: unknown): void {
    const nextGlobalStateMode = resolveGlobalStateMode(payload)
    if (nextGlobalStateMode) {
      setGlobalStateMode(nextGlobalStateMode)
    }

    if (!debugMode) {
      return
    }

    const debugPayload = payload && typeof payload === 'object' ? (payload as { debug?: unknown }).debug : undefined
    if (typeof debugPayload === 'undefined') {
      return
    }

    const parsedLoopDebug = toLoopDebugState(payload)
    setLoopDebug(parsedLoopDebug)
  }

  useEffect(() => {
    if (!hasToken) {
      return
    }

    let logoutTriggered = false
    const bestEffortLogout = () => {
      if (logoutTriggered) {
        return
      }
      logoutTriggered = true

      void resetSession({ keepalive: true, timeoutMs: 1500 })
      void supabase.auth.signOut().catch(() => {
        // best-effort logout on tab close
      })
    }

    const removeSessionResetListeners = installSessionResetOnPageExit(bestEffortLogout)
    const handleBeforeUnload = () => {
      bestEffortLogout()
    }

    window.addEventListener('beforeunload', handleBeforeUnload)

    return () => {
      removeSessionResetListeners()
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [hasToken])

  useEffect(() => {
    if (!hasToken) {
      setPendingMerchantAliasesCount(0)
      setPendingCategorizationCount(0)
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

    void refreshPendingCategorizationStatus()

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
      const finalUrl = resolvePdfReportUrl(pdfUiRequest.url, apiBaseUrl)
      openPdfFromUrl(finalUrl).catch((caughtError) => {
        setError(caughtError instanceof Error ? caughtError.message : 'Impossible d’ouvrir le rapport PDF')
      })
    }
  }, [apiBaseUrl, messages])

  useEffect(() => {
    if (!hasToken || didInitConversationRef.current) {
      return
    }

    didInitConversationRef.current = true
    void startConversation()
  }, [hasToken])

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
    segments.forEach((segment) => {
      assistantQueueRef.current.push({
        id: crypto.randomUUID(),
        content: segment,
        toolResult,
        plan,
        debugPayload,
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

  function resetLocalChatState(): void {
    setMessages([])
    setMessage('')
    setError(null)
    setToast(null)
    setLoopDebug(null)
    setGlobalStateMode(null)
    setIsImportDialogOpen(false)
    setAutoOpenImportPicker(false)
    setAwaitingPendingCategorizationReply(false)
    setPendingCategorizationCount(0)
    setResolvePendingAliasesFeedback(null)
    setTypingCursor((value) => value + 1)

    executedPdfMessageIdsRef.current.clear()
    revealedMessageIdsRef.current.clear()
    assistantQueueRef.current = []
    uploadMessageGuardsRef.current.clear()
    hasPromptedPendingCategorizationRef.current = false
    lastPromptedPendingCategorizationCountRef.current = 0
    previousIntentMessageIdRef.current = null

    for (const storageKey of CHAT_LOCAL_STORAGE_KEYS) {
      localStorage.removeItem(storageKey)
    }
  }

  async function handleHardReset() {
    if (!window.confirm('Confirmer le reset complet des données de votre profil de test ?')) return
    if (!window.confirm('Dernière confirmation: cette action est irréversible. Continuer ?')) return

    setError(null)
    resetLocalChatState()
    didInitConversationRef.current = false

    try {
      await hardResetProfile()
      await resetSession({ timeoutMs: 1500 })
      if (hasToken) {
        await startConversation()
      }
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      if (caughtError instanceof Error && (errorMessage.includes('(404)') || errorMessage.includes('Not found'))) {
        setError('Endpoint de debug désactivé (DEBUG_ENDPOINTS_ENABLED=true requis côté backend).')
        return
      }
      setError(errorMessage)
    }
  }

  async function refreshPendingCategorizationStatus(options?: { withPrompt?: boolean }) {
    if (!hasToken) {
      setPendingCategorizationCount(0)
      return
    }

    try {
      const pending = await fetchPendingTransactions()
      const twintCount = Math.max(0, Number(pending.count_twint_p2p_pending || 0))
      setPendingCategorizationCount(twintCount)
      if (
        options?.withPrompt
        && twintCount > 0
        && !awaitingPendingCategorizationReply
        && (!hasPromptedPendingCategorizationRef.current || twintCount > lastPromptedPendingCategorizationCountRef.current)
      ) {
        hasPromptedPendingCategorizationRef.current = true
        lastPromptedPendingCategorizationCountRef.current = twintCount
        setAwaitingPendingCategorizationReply(true)
        setMessages((previous) => [
          ...previous,
          {
            id: crypto.randomUUID(),
            role: 'assistant' as const,
            content: `J’ai détecté ${twintCount} paiements TWINT à catégoriser. Tu veux t’en occuper maintenant ?`,
            createdAt: Date.now(),
            toolResult: {
              type: 'ui_action',
              action: 'quick_replies',
              options: [
                { id: 'pending-cat-yes', label: '✅', value: 'oui' },
                { id: 'pending-cat-no', label: '❌', value: 'non' },
              ],
            },
          },
        ])
      }
    } catch {
      setPendingCategorizationCount(0)
    }
  }

  async function submitQuickReply(displayMessage: string, apiMessage: string) {
    if (isLoading || isImportRequired || composerMode !== 'quick_replies') {
      return
    }

    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user' as const, content: displayMessage, createdAt: Date.now() }])
    setMessage('')
    setError(null)
    setIsLoading(true)

    try {
      if (awaitingPendingCategorizationReply) {
        setAwaitingPendingCategorizationReply(false)
        lastPromptedPendingCategorizationCountRef.current = pendingCategorizationCount
        const followup = apiMessage === 'oui' ? 'OK, je t’affiche la liste (bientôt).' : 'OK, on fera ça plus tard.'
        setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'assistant' as const, content: followup, createdAt: Date.now() }])
        return
      }

      const response = await sendChatMessage(apiMessage, { debug: debugMode })
      syncLoopDebug(response)
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
      await refreshPendingCategorizationStatus({ withPrompt: true })
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }


  async function submitForm(formId: string, values: Record<string, unknown>) {
    if (isLoading || isImportRequired || composerMode !== 'form') {
      return
    }

    const { humanText, messageToBackend } = buildFormSubmitPayload(formId, values)
    setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: 'user' as const, content: humanText, createdAt: Date.now() }])
    setMessage('')
    setError(null)
    setIsLoading(true)

    setIsOptimisticallySubmittingForm(true)

    try {
      const response = await sendChatMessage(messageToBackend, { debug: debugMode })
      syncLoopDebug(response)
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsOptimisticallySubmittingForm(false)
      setIsLoading(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (composerMode !== 'text') {
      return
    }
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
      syncLoopDebug(response)
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
      syncLoopDebug(response)
      const segments = splitAssistantReply(response.reply)
      enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
      await refreshPendingCategorizationStatus({ withPrompt: true })
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Erreur inconnue')
    } finally {
      setIsLoading(false)
    }
  }

  function computeProgressStepLabel(stepIndex: number, steps: string[]): string {
    if (stepIndex < 0 || stepIndex >= steps.length) {
      return 'Terminé'
    }
    return steps[stepIndex] ?? 'Import en cours'
  }

  function buildProgressToolResult(percent: number, stepIndex: number, steps: string[]): ProgressUiAction {
    return {
      type: 'ui_action',
      action: 'progress',
      percent,
      step_label: computeProgressStepLabel(stepIndex, steps),
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
    const acceptedTypes = sanitizeImportAcceptedTypes(intent?.acceptedTypes)
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
    const steps = ['Lecture du fichier', 'Envoi au serveur', 'Traitement des transactions', 'Finalisation']
    setMessages((previous) => [
      ...previous,
      {
        id: progressId,
        role: 'assistant' as const,
        content: 'Import en cours… Je prépare ton relevé.',
        createdAt: Date.now(),
        toolResult: buildProgressToolResult(5, 0, steps),
      },
    ])

    setError(null)
    void (async () => {
      try {
        const contentBase64 = await readFileAsBase64(file)
        updateProgressMessage(progressId, buildProgressToolResult(25, 1, steps), 'Import en cours… Je prépare ton relevé.')
        updateProgressMessage(progressId, buildProgressToolResult(60, 2, steps), 'Import en cours… Je prépare ton relevé.')
        const result = await importReleves({
          files: [{ filename: file.name, content_base64: contentBase64 }],
          import_mode: 'commit',
          modified_action: 'replace',
        })

        updateProgressMessage(progressId, buildProgressToolResult(90, 3, steps), 'Import en cours… Je prépare ton relevé.')

        if (isImportClarificationResult(result)) {
          updateProgressMessage(progressId, { ...buildProgressToolResult(100, steps.length - 1, steps), step_label: 'Terminé' })
          replaceProgressWithAssistantMessage(progressId, result.message)
          setToast({ type: 'success', message: 'Choix du compte requis.' })
          return
        }

        if (isImportErrorResult(result)) {
          const message = result.message ?? result.error?.message ?? 'Erreur pendant l’import.'
          updateProgressMessage(progressId, { ...buildProgressToolResult(100, steps.length - 1, steps), step_label: 'Terminé' })
          replaceProgressWithAssistantMessage(progressId, buildImportErrorText(message), result)
          setToast({ type: 'error', message })
          return
        }

        updateProgressMessage(progressId, { ...buildProgressToolResult(100, steps.length - 1, steps), step_label: 'Terminé' })
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
        const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
        syncLoopDebug(response)
        const segments = splitAssistantReply(response.reply)
        enqueueAssistantMessages(segments, response.tool_result, response.plan, response)
        await refreshPendingCategorizationStatus({ withPrompt: true })
      } catch (caughtError) {
        const message = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue pendant l’import'
        updateProgressMessage(progressId, { ...buildProgressToolResult(100, steps.length - 1, steps), step_label: 'Terminé' }, 'Import terminé.')
        replaceProgressWithAssistantMessage(progressId, buildImportErrorText(message))
        setToast({ type: 'error', message })
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
        pendingCategorizationCount={pendingCategorizationCount}
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
        <ChatHeader onLogout={handleLogout} debugMode={debugMode} loopDebug={loopDebug} />

        <MessageList
          messages={messages}
          isLoading={isLoading}
          debugMode={debugMode}
          apiBaseUrl={apiBaseUrl}
          typingCursor={typingCursor}
          revealedMessageIdsRef={revealedMessageIdsRef}
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
          onTypingDone={(_messageId) => setTypingCursor((value) => value + 1)}
        />

        <ComposerArea
          composerMode={composerMode}
          quickReplyAction={quickReplyAction}
          formUiAction={formUiAction}
          isLoading={isLoading}
          message={message}
          setMessage={setMessage}
          isImportRequired={isImportRequired}
          isGuidedMode={isGuidedMode}
          showGuidedPlaceholder={shouldShowGuidedPlaceholder}
          onSubmit={handleSubmit}
          onSubmitQuickReply={(option) => {
            const normalizedValue = option.value.trim().toLowerCase()
            const displayMessage =
              normalizedValue === 'oui'
                ? 'Oui ✅'
                : normalizedValue === 'non'
                  ? 'Non ❌'
                  : normalizedValue === 'corriger_nom'
                    ? 'Je veux corriger mon prénom/nom.'
                    : normalizedValue === 'corriger_date'
                      ? 'Je veux corriger ma date de naissance.'
                      : option.label
            const apiMessage = option.value
            void submitQuickReply(displayMessage, apiMessage)
          }}
          onSubmitForm={(formId, values) => {
            void submitForm(formId, values)
          }}
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
  pendingCategorizationCount: number
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
  const handleToggleDebug = (nextValue: boolean) => {
    props.setDebugMode(nextValue)
  }

  return (
    <aside className={`sidebar ${props.isOpen ? 'open' : ''}`}>
      <section className="card sidebar-card">
        <h2>Profil & Actions</h2>
        <p className="status-badge">{props.statusBadge}</p>
        {props.pendingCategorizationCount > 0 ? <p className="subtle-text">À catégoriser (TWINT): {props.pendingCategorizationCount}</p> : null}

        <label className="switch-row">
          <input type="checkbox" checked={props.debugMode} onChange={(event) => handleToggleDebug(event.target.checked)} />
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

function ChatHeader({ onLogout, debugMode, loopDebug }: { onLogout: () => void; debugMode: boolean; loopDebug: LoopDebugState | null }) {
  const loopBadge = loopDebug ?? { loopId: null, step: null, blocking: null }

  return (
    <header className="chat-header sticky-top">
      <div>
        <h1>Assistant financier IA</h1>
        <p className="subtle-text">Analyse tes relevés, classe tes dépenses et répond à tes questions rapidement.</p>
        {debugMode ? (
          <p className="loop-debug-badge">
            Loop: {loopBadge.loopId ?? 'none'} · step: {loopBadge.step ?? 'none'} · blocking: {loopBadge.blocking === null ? 'unknown' : String(loopBadge.blocking)}
          </p>
        ) : null}
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
  onImportNow: (intent: ImportIntent) => void
  onTypingDone: (messageId: string) => void
  onActiveTypingChange?: (messageId: string | null) => void
}

function MessageRow({
  chatMessage,
  activeTypingMessageId,
  debugMode,
  onImportNow,
  apiBaseUrl,
  revealedMessageIdsRef,
  onTypingDone,
}: {
  chatMessage: ChatMessage
  activeTypingMessageId: string | null
  debugMode: boolean
  onImportNow: (intent: ImportIntent) => void
  apiBaseUrl: string
  revealedMessageIdsRef: RefObject<Set<string>>
  onTypingDone: (messageId: string) => void
}) {
  const isRevealed = revealedMessageIdsRef.current?.has(chatMessage.id) ?? false
  if (chatMessage.role === 'assistant' && !isRevealed && chatMessage.id !== activeTypingMessageId) {
    return null
  }

  return (
    <MessageBubble
      message={chatMessage}
      debugMode={debugMode}
      onImportNow={onImportNow}
      apiBaseUrl={apiBaseUrl}
      revealedMessageIdsRef={revealedMessageIdsRef}
      isActiveTyping={chatMessage.id === activeTypingMessageId}
      onTypingDone={onTypingDone}
    />
  )
}

export function MessageList({
  messages,
  isLoading,
  debugMode,
  apiBaseUrl,
  typingCursor,
  revealedMessageIdsRef,
  onImportNow,
  onTypingDone,
  onActiveTypingChange,
}: MessageListProps) {
  const virtuosoRef = useRef<VirtuosoHandle | null>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)

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

  const canScrollToBottom = messages.length > 0

  const handleScrollToBottom = () => {
    if (!canScrollToBottom) {
      return
    }

    virtuosoRef.current?.scrollToIndex({
      index: messages.length - 1,
      align: 'end',
      behavior: 'smooth',
    })
  }

  return (
    <div className="messages-viewport card" aria-live="polite">
      {messages.length === 0 ? <p className="subtle-text">Initialisation…</p> : null}
      <Virtuoso
        ref={virtuosoRef}
        className="messages"
        style={{ height: '100%' }}
        data={messages}
        atBottomStateChange={setIsAtBottom}
        itemContent={(_index, chatMessage) => (
          <div className="message-row">
            <MessageRow
              chatMessage={chatMessage}
              activeTypingMessageId={activeTypingMessageId}
              debugMode={debugMode}
              onImportNow={onImportNow}
              apiBaseUrl={apiBaseUrl}
              revealedMessageIdsRef={revealedMessageIdsRef}
              onTypingDone={onTypingDone}
            />
          </div>
        )}
        followOutput={(atBottom) => (atBottom ? 'smooth' : false)}
        components={{
          Header: () => <div style={{ height: 16 }} />,
          Footer: () => (
            <>
              {isLoading ? (
                <div className="loading-state">
                  <span className="spinner" />
                  <p className="subtle-text">L’assistant réfléchit…</p>
                </div>
              ) : null}
              <div style={{ height: 14 }} />
            </>
          ),
        }}
      />
      {!isAtBottom && canScrollToBottom ? (
        <button
          type="button"
          className="scroll-to-bottom-button"
          onClick={handleScrollToBottom}
          aria-label="Aller au dernier message"
          title="Aller en bas"
        >
          ↓
        </button>
      ) : null}
    </div>
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
}: {
  message: ChatMessage
  apiBaseUrl: string
  revealedMessageIdsRef: RefObject<Set<string>>
  isActiveTyping: boolean
  onTypingDone: (messageId: string) => void
}) {
  const shouldBypassTyping = shouldBypassTypingInTests()
  const completionNotifiedRef = useRef(false)
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
  }, [isActiveTyping, shouldBypassTyping, message.id, message.content, onTypingDone, revealedMessageIdsRef])


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
}: {
  message: ChatMessage
  debugMode: boolean
  onImportNow: (intent: ImportIntent) => void
  apiBaseUrl: string
  revealedMessageIdsRef: RefObject<Set<string>>
  isActiveTyping: boolean
  onTypingDone: (messageId: string) => void
}) {
  const dateLabel = new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const pdfUiRequest = toPdfUiRequest(message.toolResult)
  const resolvedPdfUrl = pdfUiRequest ? resolvePdfReportUrl(pdfUiRequest.url, apiBaseUrl) : null
  const hasPdfAction = pdfUiRequest !== null
  const spendingReportParams = pdfUiRequest ? parseSpendingReportParams(pdfUiRequest.url) : null
  const [spendingReport, setSpendingReport] = useState<SpendingReport | null>(null)
  const [spendingReportError, setSpendingReportError] = useState<string | null>(null)
  const importUiAction = toOpenImportPanelUiAction(message.toolResult)
  const importUiRequest = toLegacyImportUiRequest(message.toolResult)
  const importIntent: ImportIntent | null = importUiAction
    ? {
        messageId: message.id,
        acceptedTypes: sanitizeImportAcceptedTypes(importUiAction.accepted_types),
        source: 'ui_action',
      }
    : importUiRequest
      ? {
          messageId: message.id,
          acceptedTypes: sanitizeImportAcceptedTypes(importUiRequest.accepted_types),
          source: 'ui_request',
        }
      : null

  const progressUiAction = message.role === 'assistant' ? toProgressUiAction(message.toolResult) : null
  const matchedStepIndex = progressUiAction
    ? progressUiAction.steps.findIndex((step) => step === progressUiAction.step_label)
    : -1
  const activeStepIndex = progressUiAction
    ? progressUiAction.step_label === 'Terminé'
      ? progressUiAction.steps.length - 1
      : Math.max(0, matchedStepIndex)
    : -1
  const displayedStepIndex = progressUiAction
    ? progressUiAction.step_label === 'Terminé'
      ? progressUiAction.steps.length
      : matchedStepIndex >= 0
        ? matchedStepIndex + 1
        : 1
    : 0

  useEffect(() => {
    if (!spendingReportParams) {
      setSpendingReport(null)
      setSpendingReportError(null)
      return
    }

    let isActive = true
    setSpendingReportError(null)

    void getSpendingReport(spendingReportParams, apiBaseUrl)
      .then((report) => {
        if (!isActive) {
          return
        }
        setSpendingReport(report)
      })
      .catch((error: unknown) => {
        if (!isActive) {
          return
        }
        setSpendingReport(null)
        setSpendingReportError(error instanceof Error ? error.message : 'Impossible de charger le rapport JSON.')
      })

    return () => {
      isActive = false
    }
  }, [apiBaseUrl, pdfUiRequest?.url, spendingReportParams?.end_date, spendingReportParams?.month, spendingReportParams?.start_date])

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
                void openPdfFromUrl(resolvePdfReportUrl(pdfUiRequest.url, apiBaseUrl))
              }
            }}
            onKeyDown={(event) => {
              if ((event.key === 'Enter' || event.key === ' ') && pdfUiRequest) {
                event.preventDefault()
                void openPdfFromUrl(resolvePdfReportUrl(pdfUiRequest.url, apiBaseUrl))
              }
            }}
          >
            PDF
          </span>
        ) : null}
      </div>
      {spendingReport ? (
        <section className="report-summary" aria-label="Résumé rapport dépenses">
          <p className="subtle-text">Période: {spendingReport.period.label}</p>
          <p className="subtle-text">Total dépenses: {formatMoney(spendingReport.total, spendingReport.currency)}</p>
          <p className="subtle-text">Nb opérations: {spendingReport.count}</p>
          <div className="shared-spending-block">
            <p><strong>Dépenses partagées / Total effectif</strong></p>
            <p className="subtle-text">Partage sortant: {formatMoney(spendingReport.effective_spending.outgoing, spendingReport.currency)}</p>
            <p className="subtle-text">Partage entrant: {formatMoney(spendingReport.effective_spending.incoming, spendingReport.currency)}</p>
            <p className="subtle-text">Solde partage: {formatMoney(spendingReport.effective_spending.net_balance, spendingReport.currency)}</p>
            <p><strong>Total effectif: {formatMoney(spendingReport.effective_spending.effective_total, spendingReport.currency)}</strong></p>
          </div>
          {pdfUiRequest ? (
            <a className="secondary-button" href={resolvedPdfUrl ?? undefined} target="_blank" rel="noreferrer" onClick={(event) => {
              event.preventDefault()
              if (resolvedPdfUrl) {
                void openPdfFromUrl(resolvedPdfUrl)
              }
            }}>
              Ouvrir PDF
            </a>
          ) : null}
        </section>
      ) : null}
      {spendingReportError && hasPdfAction ? (
        <section className="report-summary" aria-label="Erreur rapport dépenses">
          <p className="subtle-text">Impossible de charger le rapport détaillé. Utilise le PDF.</p>
          <a className="secondary-button" href={resolvedPdfUrl ?? undefined} target="_blank" rel="noreferrer" onClick={(event) => {
            event.preventDefault()
            if (resolvedPdfUrl) {
              void openPdfFromUrl(resolvedPdfUrl)
            }
          }}>
            Ouvrir PDF
          </a>
        </section>
      ) : null}
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


type ComposerAreaProps = {
  composerMode: ComposerMode
  quickReplyAction: ReturnType<typeof toQuickReplyYesNoUiAction>
  formUiAction: ReturnType<typeof toFormUiAction>
  isLoading: boolean
  message: string
  setMessage: (next: string) => void
  isImportRequired: boolean
  isGuidedMode: boolean
  showGuidedPlaceholder: boolean
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onSubmitQuickReply: (option: { id: string; label: string; value: string }) => void
  onSubmitForm: (formId: string, values: Record<string, unknown>) => void
}

function ComposerArea({
  composerMode,
  quickReplyAction,
  formUiAction,
  isLoading,
  message,
  setMessage,
  isImportRequired,
  isGuidedMode,
  showGuidedPlaceholder,
  onSubmit,
  onSubmitQuickReply,
  onSubmitForm,
}: ComposerAreaProps) {
  return (
    <div className="composer-area sticky-bottom" aria-label={`Composer mode ${composerMode}`}>
      {composerMode === 'form' ? (
        <FormCard formUiAction={formUiAction} isLoading={isLoading} onSubmitForm={onSubmitForm} />
      ) : null}
      {composerMode === 'quick_replies' ? (
        <QuickReplyBar quickReplyAction={quickReplyAction} isLoading={isLoading} disabled={isImportRequired} onSubmitQuickReply={onSubmitQuickReply} />
      ) : null}
      {composerMode === 'text' && !isGuidedMode ? (
        <Composer message={message} setMessage={setMessage} onSubmit={onSubmit} isLoading={isLoading} disabled={isImportRequired} />
      ) : null}
      {showGuidedPlaceholder ? <div className="guided-placeholder subtle-text">Suis les boutons ci-dessous pour continuer.</div> : null}
    </div>
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

function FormCard({
  formUiAction,
  isLoading,
  onSubmitForm,
}: {
  formUiAction: ReturnType<typeof toFormUiAction>
  isLoading: boolean
  onSubmitForm: (formId: string, values: Record<string, unknown>) => void
}) {
  if (!formUiAction) {
    return null
  }

  const bankSelectField = formUiAction.form_id === 'onboarding_bank_accounts'
    ? formUiAction.fields.find((field) => field.id === 'selected_banks' && field.type === 'multi_select')
    : null

  return (
    <form
      className="form-composer card"
      aria-label={`Formulaire ${formUiAction.form_id}`}
      onSubmit={(event) => {
        event.preventDefault()
        if (isLoading) {
          return
        }
        const formData = new FormData(event.currentTarget)

        if (formUiAction.form_id === 'onboarding_bank_accounts') {
          const selectedBanks = (bankSelectField?.options ?? [])
            .map((option) => option.value)
            .filter((optionValue) => formData.get(`bank_${optionValue}`) === 'on')
          if (selectedBanks.length === 0) {
            return
          }
          onSubmitForm(formUiAction.form_id, {
            selected_banks: selectedBanks,
          })
          return
        }

        const values: Record<string, string> = {}
        for (const field of formUiAction.fields) {
          values[field.id] = String(formData.get(field.id) ?? '').trim()
          if (field.required && values[field.id].length === 0) {
            return
          }
        }
        onSubmitForm(formUiAction.form_id, values)
      }}
    >
      <h3 className="form-title">{formUiAction.title}</h3>
      {formUiAction.form_id === 'onboarding_bank_accounts' ? (
        <div className="form-fields">
          <fieldset className="form-field">
            <legend>{bankSelectField?.label ?? 'Banques utilisées'}</legend>
            <div className="checkbox-grid">
              {(bankSelectField?.options ?? []).map((option) => (
                <label key={option.id} className="checkbox-option">
                  <input name={`bank_${option.value}`} type="checkbox" disabled={isLoading} />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      ) : (
        <div className={`form-fields ${formUiAction.fields.length > 1 ? 'form-fields-inline' : ''}`}>
          {formUiAction.fields.map((field) => (
            <label key={field.id} className="form-field">
              {field.label}
              <input
                name={field.id}
                type={field.type}
                required={field.required}
                placeholder={field.placeholder}
                defaultValue={field.default_value ?? field.value ?? ''}
                disabled={isLoading}
              />
            </label>
          ))}
        </div>
      )}
      <div className="form-actions">
        <button type="submit" className="send-icon-button" disabled={isLoading} aria-label={formUiAction.submit_label}>
          ➤
        </button>
      </div>
    </form>
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
    <form onSubmit={onSubmit} className="composer">
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

  const acceptedTypes = sanitizeImportAcceptedTypes(pendingImportIntent?.acceptedTypes)
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
      onImportError('Format invalide. Pour l’instant, seul le format CSV est supporté.')
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
        <p className="subtle-text">Importe un relevé mensuel au format CSV.</p>

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
