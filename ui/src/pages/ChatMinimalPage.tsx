import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { createImportJob, finalizeImportJobChat, hardResetProfile, sendChatMessage, streamImportJobEvents, uploadImportFileToJob } from '../api/agentApi'
import { ChatInteractiveCard } from '../chat/ChatInteractiveCard'
import { InlineAction } from '../chat/InlineAction'
import { ReportPage } from './ReportPage'
import { normalizeQuickReplyDisplay } from '../chat/formatters'
import { shouldRenderImportEvent } from '../chat/importEventVisibility'
import { supabase } from '../lib/supabaseClient'
import {
  type QuickReplyYesNoUiAction,
  toFormUiAction,
  toLegacyImportUiRequest,
  toOpenImportPanelUiAction,
  toQuickReplyYesNoUiAction,
  toReportUiRequest,
} from './chatUiRequests'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  toolResult?: Record<string, unknown> | null
  fromQuickReply?: boolean
}

type ChatMinimalPageProps = {
  email?: string
}

type PersistedChatState = {
  version: 1
  messages: ChatMessage[]
  debugMode?: boolean
  debugUnlocked?: boolean
  activeThread?: string
  reportWasOpened?: boolean
}

const ASSISTANT_STEP_DELAY_MS = 1000
const DEFAULT_ASSISTANT_ACTION_MESSAGE = 'Je te propose les choix suivants :'
const CHAT_STORAGE_PREFIX = 'af_chat_history:v1'
const CHAT_STORAGE_VERSION = 1
const MAX_PERSISTED_MESSAGES = 300

function createMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

function buildChatStorageKey(userId: string, threadId: string): string {
  return `${CHAT_STORAGE_PREFIX}:${userId}:${threadId}`
}

function isValidChatMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== 'object') {
    return false
  }

  const message = value as Partial<ChatMessage>
  const validRole = message.role === 'assistant' || message.role === 'user'
  return typeof message.id === 'string'
    && validRole
    && typeof message.content === 'string'
    && typeof message.createdAt === 'number'
}

function getLastInteractiveAssistantMessageId(messages: ChatMessage[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === 'assistant' && message.toolResult) {
      return message.id
    }
  }

  return null
}

function loadPersistedChatState(userId: string, threadId: string): PersistedChatState | null {
  try {
    const raw = window.localStorage.getItem(buildChatStorageKey(userId, threadId))
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<PersistedChatState>
    if (parsed.version !== CHAT_STORAGE_VERSION || !Array.isArray(parsed.messages)) {
      return null
    }

    const validMessages = parsed.messages.filter(isValidChatMessage).slice(-MAX_PERSISTED_MESSAGES)

    return {
      version: CHAT_STORAGE_VERSION,
      messages: validMessages,
      debugMode: parsed.debugMode === undefined ? undefined : Boolean(parsed.debugMode),
      debugUnlocked: parsed.debugUnlocked === undefined ? undefined : Boolean(parsed.debugUnlocked),
      activeThread: typeof parsed.activeThread === 'string' ? parsed.activeThread : undefined,
      reportWasOpened: parsed.reportWasOpened === undefined ? undefined : Boolean(parsed.reportWasOpened),
    }
  } catch {
    return null
  }
}

function savePersistedChatState(userId: string, threadId: string, payload: PersistedChatState): void {
  try {
    const normalizedPayload: PersistedChatState = {
      ...payload,
      version: CHAT_STORAGE_VERSION,
      messages: payload.messages.slice(-MAX_PERSISTED_MESSAGES),
    }
    window.localStorage.setItem(buildChatStorageKey(userId, threadId), JSON.stringify(normalizedPayload))
  } catch {
    // noop: localStorage can fail (quota, private mode)
  }
}


export function ChatMinimalPage({ email }: ChatMinimalPageProps) {
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const profileMenuRef = useRef<HTMLDivElement | null>(null)
  const [activeTab, setActiveTab] = useState<'af' | 'help' | 'report'>('af')
  const [messagesAf, setMessagesAf] = useState<ChatMessage[]>([])
  const [messagesHelp, setMessagesHelp] = useState<ChatMessage[]>([])
  const [currentUserId, setCurrentUserId] = useState<string | null>(null)
  const [debugUnlocked, setDebugUnlocked] = useState(false)
  const [didHydrateFromStorage, setDidHydrateFromStorage] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isAssistantTyping, setIsAssistantTyping] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [canScroll, setCanScroll] = useState(false)
  const [debugMode, setDebugMode] = useState(() => localStorage.getItem('ui_debug_mode') === 'true')
  const [submitErrorMessage, setSubmitErrorMessage] = useState<string | null>(null)
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false)
  const [pendingActionMessageId, setPendingActionMessageId] = useState<string | null>(null)
  const [revealedActionMessageId, setRevealedActionMessageId] = useState<string | null>(null)
  const [reportAvailable, setReportAvailable] = useState(false)
  const [reportWasOpened, setReportWasOpened] = useState(false)
  const [reportParams, setReportParams] = useState<{ month?: string; start_date?: string; end_date?: string; bank_account_id?: string }>({})
  const isMountedRef = useRef(true)
  const assistantSequenceRef = useRef(0)
  const lastProgressMessageIdRef = useRef<string | null>(null)
  const importMessageIdsRef = useRef<string[]>([])

  const displayedMessages = activeTab === 'af' ? messagesAf : messagesHelp
  const shouldHighlightReportTab = reportAvailable && !reportWasOpened

  const shouldShowReportViewedQuickReply = useMemo(() => {
    if (!reportAvailable || !reportWasOpened) {
      return false
    }

    const alreadyConfirmed = messagesAf.some(
      (message) => message.role === 'user' && (message.content === 'J’ai consulté mon rapport.' || message.content === 'j_ai_consulte_mon_rapport'),
    )
    if (alreadyConfirmed) {
      return false
    }

    let candidateAssistantIndex = -1
    for (let index = messagesAf.length - 1; index >= 0; index -= 1) {
      const message = messagesAf[index]
      if (message.role !== 'assistant') {
        continue
      }

      const hasOpenReportToolResult = Boolean(message.toolResult && toReportUiRequest(message.toolResult))
      const hasReportPrompt = /ouvre(?:-le)?\s+le\s+rapport|ouvre-le/i.test(message.content)
      if (hasOpenReportToolResult || hasReportPrompt) {
        candidateAssistantIndex = index
        break
      }
    }

    if (candidateAssistantIndex === -1) {
      return false
    }

    return !messagesAf.some((message, index) => message.role === 'user' && index > candidateAssistantIndex)
  }, [messagesAf, reportAvailable, reportWasOpened])

  const pendingInteractiveIndex = useMemo(() => {
    let actionIndex = -1
    for (let index = messagesAf.length - 1; index >= 0; index -= 1) {
      const message = messagesAf[index]
      if (message.role !== 'assistant' || !message.toolResult) {
        continue
      }

      const isActionable = Boolean(
        toFormUiAction(message.toolResult)
        || toQuickReplyYesNoUiAction(message.toolResult)
        || toOpenImportPanelUiAction(message.toolResult)
        || toLegacyImportUiRequest(message.toolResult)
      )
      if (isActionable) {
        actionIndex = index
        break
      }
    }

    if (actionIndex === -1) {
      return -1
    }

    const hasUserResponseAfter = messagesAf.some((message, index) => message.role === 'user' && index > actionIndex)
    return hasUserResponseAfter ? -1 : actionIndex
  }, [messagesAf])

  function isInlineActionableToolResult(toolResult: Record<string, unknown>): boolean {
    return Boolean(
      toOpenImportPanelUiAction(toolResult)
      || toLegacyImportUiRequest(toolResult),
    )
  }

  function isFormToolResult(toolResult: Record<string, unknown>): boolean {
    return Boolean(toFormUiAction(toolResult))
  }

  function toQuickRepliesOptions(toolResult: Record<string, unknown>): QuickReplyYesNoUiAction['options'] {
    return toQuickReplyYesNoUiAction(toolResult)?.options ?? []
  }


  function handleScroll() {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, scrollTop, clientHeight } = scrollRef.current
    const distanceBottom = scrollHeight - (scrollTop + clientHeight)
    setIsNearBottom(distanceBottom < 80)
    setCanScroll(scrollHeight > clientHeight + 8)
  }

  function scrollToBottom() {
    if (!scrollRef.current) {
      return
    }

    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }

  async function wait(ms: number): Promise<void> {
    await new Promise((resolve) => {
      window.setTimeout(resolve, ms)
    })
  }

  function isSequenceCancelled(sequenceId: number): boolean {
    return !isMountedRef.current || assistantSequenceRef.current !== sequenceId
  }

  function splitAssistantReply(reply: string): string[] {
    return reply
      .split('\n\n')
      .map((segment) => segment.trim())
      .filter((segment) => segment.length > 0)
  }

  async function appendAssistantSegments(segments: string[], toolResult?: Record<string, unknown> | null): Promise<void> {
    const sequenceId = ++assistantSequenceRef.current
    const normalizedSegments = segments.length > 0
      ? segments
      : [toolResult ? DEFAULT_ASSISTANT_ACTION_MESSAGE : '...']

    for (let index = 0; index < normalizedSegments.length; index += 1) {
      const isLastSegment = index === normalizedSegments.length - 1
      const assistantMessageId = createMessageId()

      setIsAssistantTyping(true)
      await wait(ASSISTANT_STEP_DELAY_MS)
      if (isSequenceCancelled(sequenceId)) {
        return
      }

      setIsAssistantTyping(false)
      setMessagesAf((current) => [
        ...current,
        {
          id: assistantMessageId,
          role: 'assistant',
          content: normalizedSegments[index],
          createdAt: Date.now(),
          toolResult: isLastSegment ? toolResult : null,
        },
      ])

      if (isLastSegment && toolResult) {
        const reportUiRequest = toReportUiRequest(toolResult)
        if (reportUiRequest) {
          setReportAvailable(true)
          setReportParams(reportUiRequest.query)
        }
        setPendingActionMessageId(null)
        setRevealedActionMessageId(assistantMessageId)
      }
    }
  }

  async function appendAssistantReplyInSequence(reply: string, toolResult?: Record<string, unknown> | null): Promise<void> {
    const segments = splitAssistantReply(reply)
    await appendAssistantSegments(segments, toolResult)
  }

  function pushAssistantStatus(content: string): string {
    const id = createMessageId()
    setMessagesAf((current) => [
      ...current,
      {
        id,
        role: 'assistant',
        content,
        createdAt: Date.now(),
      },
    ])
    importMessageIdsRef.current.push(id)
    return id
  }

  function updateAssistantStatus(messageId: string, content: string) {
    if (!importMessageIdsRef.current.includes(messageId)) {
      importMessageIdsRef.current.push(messageId)
    }
    setMessagesAf((current) => current.map((message) => (
      message.id === messageId ? { ...message, content } : message
    )))
  }

  function clearImportStreamingMessages() {
    const ids = new Set(importMessageIdsRef.current)
    if (ids.size > 0) {
      setMessagesAf((current) => current.filter((message) => !ids.has(message.id)))
    }
    importMessageIdsRef.current = []
  }

  useEffect(() => {
    if (isNearBottom) {
      scrollToBottom()
    }
  }, [displayedMessages.length, isAssistantTyping, isNearBottom])

  useEffect(() => {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, clientHeight } = scrollRef.current
    setCanScroll(scrollHeight > clientHeight + 8)
  }, [displayedMessages.length, isAssistantTyping])

  useEffect(() => {
    localStorage.setItem('ui_debug_mode', String(debugMode))
  }, [debugMode])

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (!profileMenuRef.current) {
        return
      }
      if (!profileMenuRef.current.contains(event.target as Node)) {
        setIsProfileMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handleOutsideClick)
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick)
    }
  }, [])

  async function startConversation() {
    setMessagesAf([])
    setIsSending(false)
    try {
      const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
      setMessagesAf([])
      await appendAssistantReplyInSequence(response.reply, response.tool_result)
    } catch {
      setMessagesAf([
        {
          id: createMessageId(),
          role: 'assistant',
          content: 'Impossible de charger le message de bienvenue.',
          createdAt: Date.now(),
        },
      ])
    } finally {
      setIsAssistantTyping(false)
    }
  }

  useEffect(() => {
    isMountedRef.current = true

    async function hydrateConversation() {
      try {
        const { data } = await supabase.auth.getUser()
        if (!isMountedRef.current) {
          return
        }

        const userId = data.user?.id ?? null
        setCurrentUserId(userId)

        if (!userId) {
          await startConversation()
          return
        }

        const persistedMain = loadPersistedChatState(userId, 'main')
        const persistedHelp = loadPersistedChatState(userId, 'help')
        const restoredMain = Boolean(persistedMain && persistedMain.messages.length > 0)
        const restoredHelp = Boolean(persistedHelp && persistedHelp.messages.length > 0)

        if (persistedMain) {
          setMessagesAf(persistedMain.messages)
          const interactiveCandidateId = getLastInteractiveAssistantMessageId(persistedMain.messages)
          setRevealedActionMessageId(interactiveCandidateId)
          setPendingActionMessageId(null)
          setIsSending(false)
          setIsAssistantTyping(false)
          if (persistedMain.debugMode !== undefined) {
            setDebugMode(persistedMain.debugMode)
          }
          if (persistedMain.debugUnlocked !== undefined) {
            setDebugUnlocked(persistedMain.debugUnlocked)
          }
          if (persistedMain.reportWasOpened !== undefined) {
            setReportWasOpened(persistedMain.reportWasOpened)
          }
        }

        if (persistedHelp) {
          setMessagesHelp(persistedHelp.messages)
          if (persistedHelp.debugMode !== undefined) {
            setDebugMode(persistedHelp.debugMode)
          }
          if (persistedHelp.debugUnlocked !== undefined) {
            setDebugUnlocked(persistedHelp.debugUnlocked)
          }
        }

        const shouldOpenHelp = persistedMain?.activeThread === 'help' || persistedHelp?.activeThread === 'help'
        if (shouldOpenHelp) {
          setActiveTab('help')
        }

        if (!restoredMain && !restoredHelp) {
          await startConversation()
        }
      } catch {
        if (!isMountedRef.current) {
          return
        }
        await startConversation()
      } finally {
        if (isMountedRef.current) {
          setDidHydrateFromStorage(true)
        }
      }
    }

    void hydrateConversation()

    return () => {
      isMountedRef.current = false
      assistantSequenceRef.current += 1
    }
  }, [])

  useEffect(() => {
    for (let index = messagesAf.length - 1; index >= 0; index -= 1) {
      const toolResult = messagesAf[index].toolResult
      if (!toolResult) {
        continue
      }
      const reportUiRequest = toReportUiRequest(toolResult)
      if (reportUiRequest) {
        setReportAvailable(true)
        setReportParams(reportUiRequest.query)
        return
      }
    }
  }, [messagesAf])

  useEffect(() => {
    if (!didHydrateFromStorage || !currentUserId) {
      return
    }

    savePersistedChatState(currentUserId, 'main', {
      version: CHAT_STORAGE_VERSION,
      messages: messagesAf,
      debugMode,
      debugUnlocked,
      activeThread: activeTab,
      reportWasOpened,
    })

    savePersistedChatState(currentUserId, 'help', {
      version: CHAT_STORAGE_VERSION,
      messages: messagesHelp,
      debugMode,
      debugUnlocked,
      activeThread: activeTab,
      reportWasOpened,
    })
  }, [activeTab, currentUserId, debugMode, debugUnlocked, didHydrateFromStorage, messagesAf, messagesHelp, reportWasOpened])

  async function submitMessage(text: string, displayContent?: string, fromQuickReply = false) {
    const trimmed = text.trim()
    if (!trimmed || isSending || activeTab === 'help') {
      return
    }

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: displayContent?.trim().length ? displayContent : trimmed,
      createdAt: Date.now(),
      fromQuickReply,
    }

    setMessagesAf((current) => [...current, userMessage])
    setSubmitErrorMessage(null)
    setPendingActionMessageId(null)
    setRevealedActionMessageId(null)
    setIsSending(true)
    setIsAssistantTyping(true)

    try {
      const response = await sendChatMessage(trimmed, { debug: debugMode })
      await appendAssistantReplyInSequence(response.reply, response.tool_result)
    } catch (error) {
      const content = error instanceof Error ? error.message : 'Erreur inconnue.'
      setSubmitErrorMessage(content)
      setMessagesAf((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content,
          createdAt: Date.now(),
        },
      ])
    } finally {
      setIsSending(false)
      setIsAssistantTyping(false)
    }
  }

  async function toBase64(file: File): Promise<string> {
    const bytes = await file.arrayBuffer()
    let binary = ''
    const chunkSize = 0x8000
    const uint8Array = new Uint8Array(bytes)
    for (let index = 0; index < uint8Array.length; index += chunkSize) {
      const chunk = uint8Array.subarray(index, index + chunkSize)
      binary += String.fromCharCode(...chunk)
    }
    return btoa(binary)
  }

  async function handleImportFile(file: File) {
    if (isSending) {
      return
    }

    setSubmitErrorMessage(null)
    setMessagesAf((current) => [
      ...current,
      {
        id: createMessageId(),
        role: 'user',
        content: 'J’importe mon relevé bancaire.',
        createdAt: Date.now(),
      },
    ])

    setIsSending(true)
    setIsAssistantTyping(false)
    importMessageIdsRef.current = []

    try {
      const contentBase64 = await toBase64(file)
      const { job_id: jobId } = await createImportJob()
      await uploadImportFileToJob(jobId, {
        files: [
          {
            filename: file.name,
            content_base64: contentBase64,
          },
        ],
      })

      pushAssistantStatus('OK — import en cours. Je te tiens au courant étape par étape.')

      const displayedSeq = new Set<number>()
      let gotFirstEvent = false
      let idleMessageShown = false
      let stopStreaming: (() => void) | null = null
      let idleTimer: number | null = window.setTimeout(() => {
        if (!gotFirstEvent && !idleMessageShown) {
          idleMessageShown = true
          pushAssistantStatus('Toujours en cours…')
        }
      }, 3000)

      stopStreaming = await streamImportJobEvents(
        jobId,
        async (event) => {
          gotFirstEvent = true
          if (idleTimer !== null) {
            window.clearTimeout(idleTimer)
            idleTimer = null
          }
          if (displayedSeq.has(event.seq)) {
            return
          }
          displayedSeq.add(event.seq)

          if (!shouldRenderImportEvent(event.kind)) {
            return
          }

          if (event.kind === 'done') {
            if (stopStreaming) {
              stopStreaming()
            }
            lastProgressMessageIdRef.current = null
            clearImportStreamingMessages()
            const response = await finalizeImportJobChat(jobId)
            await appendAssistantReplyInSequence(response.reply, response.tool_result)
            setIsSending(false)
            return
          }

          const isProgressEvent = event.kind.endsWith('_progress') && event.kind !== 'bank_detected'
          if (isProgressEvent) {
            const lastProgressMessageId = lastProgressMessageIdRef.current
            if (lastProgressMessageId) {
              updateAssistantStatus(lastProgressMessageId, event.message)
            } else {
              lastProgressMessageIdRef.current = pushAssistantStatus(event.message)
            }
          } else {
            lastProgressMessageIdRef.current = null
            pushAssistantStatus(event.message)
          }

          if (event.kind === 'error') {
            if (stopStreaming) {
              stopStreaming()
            }
            setSubmitErrorMessage(event.message)
            importMessageIdsRef.current = []
            setIsSending(false)
            return
          }
        },
        (errorMessage) => {
          setSubmitErrorMessage(errorMessage)
          setMessagesAf((current) => [
            ...current,
            {
              id: createMessageId(),
              role: 'assistant',
              content: errorMessage,
              createdAt: Date.now(),
            },
          ])
          setIsSending(false)
        },
      )
    } catch (error) {
      const content = error instanceof Error ? error.message : 'Erreur inconnue.'
      setSubmitErrorMessage(content)
      setMessagesAf((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content,
          createdAt: Date.now(),
        },
      ])
      setIsSending(false)
    }
  }


  function resetLocalChatState() {
    lastProgressMessageIdRef.current = null
    setMessagesAf([])
    setIsSending(false)
    setIsAssistantTyping(false)
    setIsNearBottom(true)
    setPendingActionMessageId(null)
    setRevealedActionMessageId(null)
    setReportAvailable(false)
    setReportWasOpened(false)
    setReportParams({})

    const chatStorageKeys = ['chat_messages', 'chat_debug_payload', 'chat_console_state']
    for (const key of chatStorageKeys) {
      window.localStorage.removeItem(key)
    }

    const storagePrefix = `${CHAT_STORAGE_PREFIX}:`
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index)
      if (key && key.startsWith(storagePrefix)) {
        window.localStorage.removeItem(key)
      }
    }
  }

  async function handleHardReset() {
    if (!window.confirm('Confirmer le reset complet…')) {
      return
    }

    try {
      await hardResetProfile()
      resetLocalChatState()
      await startConversation()
    } catch (error) {
      setMessagesAf((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: error instanceof Error ? error.message : 'Échec du reset complet.',
          createdAt: Date.now(),
        },
      ])
    }
  }

  function openHelpTab() {
    setActiveTab('help')
    setIsAssistantTyping(false)

    if (currentUserId) {
      const persistedHelp = loadPersistedChatState(currentUserId, 'help')
      if (persistedHelp) {
        setMessagesHelp(persistedHelp.messages)
        if (persistedHelp.debugMode !== undefined) {
          setDebugMode(persistedHelp.debugMode)
        }
        if (persistedHelp.debugUnlocked !== undefined) {
          setDebugUnlocked(persistedHelp.debugUnlocked)
        }
        return
      }
    }

    setMessagesHelp((current) => {
      if (current.length > 0) {
        return current
      }

      return [
        {
          id: createMessageId(),
          role: 'assistant',
          content: 'Aide (bientôt) 🙂\n- Sécurité des données\n- Fonctionnement de l’IA\n- Import CSV\n\nCette section sera disponible prochainement.',
          createdAt: Date.now(),
        },
      ]
    })
  }

  async function handleLogout() {
    const { error } = await supabase.auth.signOut()
    if (error) {
      setMessagesAf((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: `Erreur lors de la déconnexion: ${error.message}`,
          createdAt: Date.now(),
        },
      ])
      return
    }

    navigate('/login', { replace: true })
  }

  return (
    <main className="chat-layout" aria-label="Chat minimal">
      <div className="chat-frame">
        <div className="chat-stack">
          <header className="chat-min-header">
            <button
              type="button"
              className={`icon-avatar-button ${activeTab === 'af' ? 'tab-button-selected' : ''}`}
              aria-label="Assistant financier"
              onClick={() => {
                setActiveTab('af')
                if (!currentUserId) {
                  return
                }
                const persistedMain = loadPersistedChatState(currentUserId, 'main')
                if (persistedMain) {
                  setMessagesAf(persistedMain.messages)
                  const interactiveCandidateId = getLastInteractiveAssistantMessageId(persistedMain.messages)
                  setRevealedActionMessageId(interactiveCandidateId)
                  setPendingActionMessageId(null)
                  if (persistedMain.debugMode !== undefined) {
                    setDebugMode(persistedMain.debugMode)
                  }
                  if (persistedMain.debugUnlocked !== undefined) {
                    setDebugUnlocked(persistedMain.debugUnlocked)
                  }
                  if (persistedMain.reportWasOpened !== undefined) {
                    setReportWasOpened(persistedMain.reportWasOpened)
                  }
                }
              }}
            >
              <span className="icon-avatar">AF</span>
            </button>
            <div className="chat-title-wrap">
              <strong className="chat-title">Assistant financier</strong>
            </div>
            <div className="chat-header-actions">
              <button
                type="button"
                className={`icon-button ${activeTab === 'help' ? 'tab-button-selected' : ''}`}
                onClick={openHelpTab}
                aria-label="Aide"
              >
                ?
              </button>
              {reportAvailable ? (
                <button
                  type="button"
                  className={`icon-button ${activeTab === 'report' ? 'tab-button-selected' : ''} ${shouldHighlightReportTab ? 'report-tab-pulse' : ''}`}
                  onClick={() => {
                    setActiveTab('report')
                    setReportWasOpened(true)
                  }}
                  aria-label="Rapport"
                >
                  📊
                </button>
              ) : null}
              <label className="debug-toggle" htmlFor="debug-mode-toggle">
                <input
                  id="debug-mode-toggle"
                  type="checkbox"
                  checked={debugMode}
                  onChange={(event) => setDebugMode(event.target.checked)}
                />
                Debug
              </label>
              {debugMode ? (
                <button type="button" className="secondary-button" onClick={() => { void handleHardReset() }}>
                  Reset
                </button>
              ) : null}
              <div className="profile-menu-wrap" ref={profileMenuRef}>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setIsProfileMenuOpen((current) => !current)}
                  aria-label="Menu profil"
                  aria-expanded={isProfileMenuOpen}
                >
                  👤
                </button>
                {isProfileMenuOpen ? (
                  <div className="profile-menu-dropdown" role="menu">
                    {email ? <p className="profile-menu-email">{email}</p> : null}
                    <button
                      type="button"
                      className="profile-menu-item"
                      role="menuitem"
                      onClick={() => {
                        setIsProfileMenuOpen(false)
                        void handleLogout()
                      }}
                    >
                      Se déconnecter
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
          </header>

          <div className={`message-area ${shouldHighlightReportTab && activeTab === 'af' ? 'message-area-blurred' : ''}`}>
            {activeTab === 'report' ? (
              <div className="chat-scroll report-scroll">
                <ReportPage params={reportParams} />
              </div>
            ) : (
              <>
                <div ref={scrollRef} className="chat-scroll" onScroll={handleScroll}>
                  {displayedMessages.map((message, index) => {
                    const isLastAssistantMessage =
                      message.role === 'assistant' && !displayedMessages.slice(index + 1).some((nextMessage) => nextMessage.role === 'assistant')
                    const isShort = message.content.trim().length <= 18 && message.content.indexOf('\n') === -1
                    const messageClasses = [
                      'msg',
                      message.role === 'user' ? 'msg-user' : 'msg-assistant',
                      ...(isShort ? ['msg-short'] : []),
                      ...(message.role === 'user' && message.fromQuickReply ? ['msg-chip'] : []),
                    ].join(' ')

                    return (
                      <div key={message.id} className="msg-row">
                        <div className={messageClasses}>{message.content}</div>
                        {message.role === 'assistant'
                        && message.toolResult
                        && activeTab === 'af'
                        && pendingInteractiveIndex === index
                        && pendingActionMessageId !== message.id
                        && revealedActionMessageId === message.id
                        && isFormToolResult(message.toolResult) ? (
                          <ChatInteractiveCard
                            toolResult={message.toolResult}
                            onSubmit={({ message: nextMessage, humanText }) => {
                              const display = humanText ?? normalizeQuickReplyDisplay(undefined, nextMessage)
                              void submitMessage(nextMessage, display, true)
                            }}
                            onImport={(file) => {
                              void handleImportFile(file)
                            }}
                          />
                        ) : null}
                        {message.role === 'assistant'
                        && message.toolResult
                        && activeTab === 'af'
                        && pendingInteractiveIndex === index
                        && pendingActionMessageId !== message.id
                        && revealedActionMessageId === message.id
                        && toQuickRepliesOptions(message.toolResult).length > 0 ? (
                          <div className="quick-replies-stack" role="group" aria-label="Réponses rapides">
                            {toQuickRepliesOptions(message.toolResult).map((option) => (
                              <button
                                key={option.id}
                                type="button"
                                className="msg msg-user msg-quick-reply msg-quick-reply--pending"
                                disabled={isSending || isAssistantTyping}
                                onClick={() => {
                                  const display = normalizeQuickReplyDisplay(option.label, option.value)
                                  void submitMessage(option.value, display, true)
                                }}
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                        {message.role === 'assistant'
                        && message.toolResult
                        && activeTab === 'af'
                        && pendingInteractiveIndex === index
                        && pendingActionMessageId !== message.id
                        && revealedActionMessageId === message.id
                        && !isFormToolResult(message.toolResult)
                        && isInlineActionableToolResult(message.toolResult)
                        && toQuickRepliesOptions(message.toolResult).length === 0 ? (
                          <InlineAction
                            actionState={message.toolResult}
                            disabled={isSending || isAssistantTyping}
                            onChoose={(value, label) => {
                              const display = label ? normalizeQuickReplyDisplay(label, value) : normalizeQuickReplyDisplay(undefined, value)
                              void submitMessage(value, display, true)
                            }}
                            onImportFile={(file) => {
                              void handleImportFile(file)
                            }}
                          />
                        ) : null}
                        {debugMode && isLastAssistantMessage ? (
                          <details>
                            <summary>Debug payload</summary>
                            <pre>{JSON.stringify(message.toolResult ?? null, null, 2)}</pre>
                          </details>
                        ) : null}
                      </div>
                    )
                  })}
                  {activeTab === 'af' && isAssistantTyping ? <div className="msg msg-assistant">...</div> : null}
                </div>
                {canScroll && !isNearBottom ? (
                  <button type="button" className="scroll-down-btn" onClick={scrollToBottom} aria-label="Aller en bas">
                    ↓
                  </button>
                ) : null}
                {activeTab === 'af' && shouldShowReportViewedQuickReply ? (
                  <div className="quick-replies-stack" role="group" aria-label="Confirmation de lecture du rapport">
                    <button
                      type="button"
                      className="msg msg-user msg-quick-reply msg-quick-reply--pending"
                      disabled={isSending || isAssistantTyping}
                      onClick={() => {
                        void submitMessage('j_ai_consulte_mon_rapport', 'J’ai consulté mon rapport.', true)
                      }}
                    >
                      J’ai consulté mon rapport.
                    </button>
                  </div>
                ) : null}
              </>
            )}
          </div>

          {submitErrorMessage ? <p className="subtle-text">{submitErrorMessage}</p> : null}
        </div>
      </div>
    </main>
  )
}
