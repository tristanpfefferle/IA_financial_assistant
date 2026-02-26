import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { createImportJob, finalizeImportJobChat, hardResetProfile, openPdfFromUrl, sendChatMessage, streamImportJobEvents, uploadImportFileToJob } from '../api/agentApi'
import { ChatInteractiveCard } from '../chat/ChatInteractiveCard'
import { InlineAction } from '../chat/InlineAction'
import { normalizeQuickReplyDisplay } from '../chat/formatters'
import { shouldRenderImportEvent } from '../chat/importEventVisibility'
import { supabase } from '../lib/supabaseClient'
import {
  toAnyPdfUiRequest,
  toFormUiAction,
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
  fromQuickReply?: boolean
}

type ChatMinimalPageProps = {
  email?: string
}

const THINKING_DELAY_MS = { min: 500, max: 900 }
const BETWEEN_SENTENCE_DELAY_MS = { min: 350, max: 650 }
function createMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

function randomDelay(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

function splitIntoSentences(text: string): string[] {
  const trimmed = text.trim()
  if (!trimmed) {
    return []
  }

  const sentences = trimmed
    .split(/(?<=[.!?…])\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence.length > 0)
    .filter((sentence) => !/^\(?\s*oui\s*\/\s*non\s*\)?\.?$/i.test(sentence))
  if (sentences.length === 0) {
    return []
  }

  return sentences.length > 1 ? sentences : [sentences[0]]
}


export function ChatMinimalPage({ email }: ChatMinimalPageProps) {
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isAssistantTyping, setIsAssistantTyping] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [canScroll, setCanScroll] = useState(false)
  const [debugUnlocked, setDebugUnlocked] = useState(() => localStorage.getItem('ui_debug_unlocked') === 'true')
  const [debugMode, setDebugMode] = useState(() => localStorage.getItem('ui_debug_mode') === 'true')
  const [headerMessage, setHeaderMessage] = useState<string | null>(null)
  const [submitErrorMessage, setSubmitErrorMessage] = useState<string | null>(null)
  const isMountedRef = useRef(true)
  const assistantSequenceRef = useRef(0)
  const lastProgressMessageIdRef = useRef<string | null>(null)
  const importMessageIdsRef = useRef<string[]>([])

  const pendingInteractiveIndex = useMemo(() => {
    let actionIndex = -1
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.role !== 'assistant' || !message.toolResult) {
        continue
      }

      const isActionable = Boolean(
        toFormUiAction(message.toolResult)
        || toQuickReplyYesNoUiAction(message.toolResult)
        || toOpenImportPanelUiAction(message.toolResult)
        || toLegacyImportUiRequest(message.toolResult)
        || toAnyPdfUiRequest(message.toolResult)
      )
      if (isActionable) {
        actionIndex = index
        break
      }
    }

    if (actionIndex === -1) {
      return -1
    }

    const hasUserResponseAfter = messages.some((message, index) => message.role === 'user' && index > actionIndex)
    return hasUserResponseAfter ? -1 : actionIndex
  }, [messages])

  function isInlineActionableToolResult(toolResult: Record<string, unknown>): boolean {
    return Boolean(
      toQuickReplyYesNoUiAction(toolResult)
      || toOpenImportPanelUiAction(toolResult)
      || toLegacyImportUiRequest(toolResult)
      || toAnyPdfUiRequest(toolResult),
    )
  }

  function isFormToolResult(toolResult: Record<string, unknown>): boolean {
    return Boolean(toFormUiAction(toolResult))
  }

  function toQuickRepliesAction(toolResult: Record<string, unknown>): Record<string, unknown> | null {
    const quickRepliesAction = toQuickReplyYesNoUiAction(toolResult)
    if (!quickRepliesAction) {
      return null
    }
    return quickRepliesAction as unknown as Record<string, unknown>
  }

  function isPdfReportRequest(toolResult: Record<string, unknown>): boolean {
    return Boolean(toPdfUiRequest(toolResult))
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

  async function appendAssistantReplyInSequence(reply: string, toolResult?: Record<string, unknown> | null): Promise<void> {
    const sequenceId = ++assistantSequenceRef.current
    const sentences = splitIntoSentences(reply)

    setIsAssistantTyping(true)
    await wait(randomDelay(THINKING_DELAY_MS.min, THINKING_DELAY_MS.max))
    if (isSequenceCancelled(sequenceId)) {
      return
    }

    for (let index = 0; index < sentences.length; index += 1) {
      const sentence = sentences[index]
      const isLastSentence = index === sentences.length - 1

      setIsAssistantTyping(false)
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: sentence,
          createdAt: Date.now(),
          toolResult: isLastSentence ? toolResult : undefined,
        },
      ])

      if (!isLastSentence) {
        setIsAssistantTyping(true)
        await wait(randomDelay(BETWEEN_SENTENCE_DELAY_MS.min, BETWEEN_SENTENCE_DELAY_MS.max))
        if (isSequenceCancelled(sequenceId)) {
          return
        }
      }
    }

    if (!isSequenceCancelled(sequenceId)) {
      setIsAssistantTyping(false)
    }
  }

  function pushAssistantStatus(content: string): string {
    const id = createMessageId()
    setMessages((current) => [
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
    setMessages((current) => current.map((message) => (
      message.id === messageId ? { ...message, content } : message
    )))
  }

  function clearImportStreamingMessages() {
    const ids = new Set(importMessageIdsRef.current)
    if (ids.size > 0) {
      setMessages((current) => current.filter((message) => !ids.has(message.id)))
    }
    importMessageIdsRef.current = []
  }

  useEffect(() => {
    if (isNearBottom) {
      scrollToBottom()
    }
  }, [messages.length, isAssistantTyping, isNearBottom])

  useEffect(() => {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, clientHeight } = scrollRef.current
    setCanScroll(scrollHeight > clientHeight + 8)
  }, [messages.length, isAssistantTyping])

  useEffect(() => {
    localStorage.setItem('ui_debug_mode', String(debugMode))
  }, [debugMode])

  useEffect(() => {
    localStorage.setItem('ui_debug_unlocked', String(debugUnlocked))
  }, [debugUnlocked])

  function handleUnlockDebug() {
    const DEBUG_UNLOCK_PIN = '1234'
    const enteredPin = window.prompt('Entrer le code PIN debug')

    if (enteredPin === DEBUG_UNLOCK_PIN) {
      setHeaderMessage(null)
      setDebugUnlocked(true)
      return
    }

    setHeaderMessage('Code incorrect')
  }

  function handleLockDebug() {
    setDebugUnlocked(false)
    setDebugMode(false)
    localStorage.removeItem('ui_debug_unlocked')
    localStorage.removeItem('ui_debug_mode')
  }

  async function startConversation() {
    setHeaderMessage(null)
    setMessages([])
    setIsSending(false)
    try {
      const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
      setMessages([])
      await appendAssistantReplyInSequence(response.reply, response.tool_result)
    } catch {
      setMessages([
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

    async function loadGreeting() {
      try {
        const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
        if (!isMountedRef.current) {
          return
        }

        setMessages([])
        await appendAssistantReplyInSequence(response.reply, response.tool_result)
      } catch {
        if (!isMountedRef.current) {
          return
        }

        setMessages([
          {
            id: createMessageId(),
            role: 'assistant',
            content: 'Impossible de charger le message de bienvenue.',
            createdAt: Date.now(),
          },
        ])
      } finally {
        if (isMountedRef.current) {
          setIsAssistantTyping(false)
        }
      }
    }

    void loadGreeting()

    return () => {
      isMountedRef.current = false
      assistantSequenceRef.current += 1
    }
  }, [])

  async function submitMessage(text: string, displayContent?: string, fromQuickReply = false) {
    const trimmed = text.trim()
    if (!trimmed || isSending) {
      return
    }

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: displayContent?.trim().length ? displayContent : trimmed,
      createdAt: Date.now(),
      fromQuickReply,
    }

    setMessages((current) => [...current, userMessage])
    setSubmitErrorMessage(null)
    setIsSending(true)
    setIsAssistantTyping(true)

    try {
      const response = await sendChatMessage(trimmed, { debug: debugMode })
      await appendAssistantReplyInSequence(response.reply, response.tool_result)
    } catch (error) {
      const content = error instanceof Error ? error.message : 'Erreur inconnue.'
      setSubmitErrorMessage(content)
      setMessages((current) => [
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
    setMessages((current) => [
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
          setMessages((current) => [
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
      setMessages((current) => [
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
    setMessages([])
    setIsSending(false)
    setIsAssistantTyping(false)
    setIsNearBottom(true)

    const chatStorageKeys = ['chat_messages', 'chat_debug_payload', 'chat_console_state']
    for (const key of chatStorageKeys) {
      window.localStorage.removeItem(key)
    }
  }

  async function handleHardReset() {
    if (!window.confirm('Confirmer le reset complet…')) {
      return
    }

    setHeaderMessage(null)
    try {
      await hardResetProfile()
      resetLocalChatState()
      await startConversation()
      setHeaderMessage('Reset terminé. Conversation redémarrée.')
    } catch (error) {
      setHeaderMessage(error instanceof Error ? error.message : 'Échec du reset complet.')
    }
  }

  async function handleLogout() {
    const { error } = await supabase.auth.signOut()
    if (error) {
      setMessages((current) => [
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
            <div className="chat-title-wrap">
              <strong>Assistant financier</strong>
              {debugUnlocked && debugMode ? <span className="debug-badge">Debug</span> : null}
            </div>
            <div className="chat-header-actions">
              <label className="debug-toggle" htmlFor="debug-mode-toggle">
                <input
                  id="debug-mode-toggle"
                  type="checkbox"
                  checked={debugMode}
                  onChange={(event) => setDebugMode(event.target.checked)}
                  disabled={!debugUnlocked}
                />
                Debug
              </label>
              {!debugUnlocked ? (
                <button type="button" className="secondary-button unlock-button" onClick={handleUnlockDebug}>
                  Déverrouiller
                </button>
              ) : (
                <button type="button" className="secondary-button" onClick={handleLockDebug}>
                  Verrouiller
                </button>
              )}
              {debugUnlocked && debugMode ? (
                <button type="button" className="secondary-button" onClick={() => { void handleHardReset() }}>
                  Reset
                </button>
              ) : null}
              {email ? <span className="subtle-text">{email}</span> : null}
              <button type="button" className="secondary-button" onClick={() => { void handleLogout() }}>
                Se déconnecter
              </button>
            </div>
          </header>
          {headerMessage ? <p className="subtle-text">{headerMessage}</p> : null}

          <div className="message-area">
            <div ref={scrollRef} className="chat-scroll" onScroll={handleScroll}>
              {messages.map((message, index) => {
                const isLastAssistantMessage =
                  message.role === 'assistant' && !messages.slice(index + 1).some((nextMessage) => nextMessage.role === 'assistant')
                const isShort = message.content.trim().length <= 18 && message.content.indexOf('\n') === -1
                const messageClasses = [
                  'msg',
                  message.role === 'user' ? 'msg-user' : 'msg-assistant',
                  ...(isShort ? ['msg-short'] : []),
                  ...(message.role === 'user' && message.fromQuickReply ? ['msg-chip'] : []),
                ].join(' ')

                return (
                  <div key={message.id}>
                    <div className={messageClasses}>{message.content}</div>
                    {message.role === 'assistant' && message.toolResult && isPdfReportRequest(message.toolResult) ? (
                      <div className="msg msg-assistant">
                        <button
                          type="button"
                          className="link-button"
                          onClick={(event) => {
                            event.preventDefault()
                            const pdfUi = toPdfUiRequest(message.toolResult as Record<string, unknown>)
                            if (!pdfUi) {
                              return
                            }
                            void openPdfFromUrl(pdfUi.url)
                          }}
                        >
                          📄 Ouvrir le rapport PDF
                        </button>
                      </div>
                    ) : null}
                    {message.role === 'assistant'
                    && message.toolResult
                    && pendingInteractiveIndex === index
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
                    && pendingInteractiveIndex === index
                    && !isFormToolResult(message.toolResult)
                    && isInlineActionableToolResult(message.toolResult)
                    && !isPdfReportRequest(message.toolResult) ? (
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
                    {message.role === 'assistant'
                    && message.toolResult
                    && pendingInteractiveIndex === index
                    && isPdfReportRequest(message.toolResult)
                    && toQuickRepliesAction(message.toolResult) ? (
                      <InlineAction
                        actionState={toQuickRepliesAction(message.toolResult) as Record<string, unknown>}
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
                    {debugUnlocked && debugMode && isLastAssistantMessage ? (
                      <details>
                        <summary>Debug payload</summary>
                        <pre>{JSON.stringify(message.toolResult ?? null, null, 2)}</pre>
                      </details>
                    ) : null}
                  </div>
                )
              })}
              {isAssistantTyping ? <div className="msg msg-assistant">...</div> : null}
            </div>
            {canScroll && !isNearBottom ? (
              <button type="button" className="scroll-down-btn" onClick={scrollToBottom} aria-label="Aller en bas">
                ↓
              </button>
            ) : null}
          </div>

          {submitErrorMessage ? <p className="subtle-text">{submitErrorMessage}</p> : null}
        </div>
      </div>
    </main>
  )
}
