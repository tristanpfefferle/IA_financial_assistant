import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { hardResetProfile, sendChatMessage } from '../api/agentApi'
import { ConsolePanel } from '../chat/ConsolePanel'
import type { ConsoleOption, ConsoleUiState } from '../chat/types'
import { supabase } from '../lib/supabaseClient'
import { toQuickReplyYesNoUiAction } from './chatUiRequests'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  toolResult?: Record<string, unknown> | null
}

type ChatMinimalPageProps = {
  email?: string
}

function createMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

function normalizeReplyValue(option: { label: string; value: string }): string {
  const normalizedValue = option.value.trim().toLowerCase()
  if (normalizedValue.length > 0) {
    return normalizedValue
  }
  return option.label.trim().toLowerCase()
}

function mapQuickReplyOption(option: { id: string; label: string; value: string }, tone?: ConsoleOption['tone']): ConsoleOption {
  return {
    ...option,
    tone: tone ?? 'neutral',
  }
}

function extractPrompt(toolResult: Record<string, unknown> | null | undefined): string | undefined {
  if (!toolResult) {
    return undefined
  }
  return typeof toolResult.prompt === 'string' && toolResult.prompt.trim().length > 0 ? toolResult.prompt : undefined
}

function extractConsoleState(messages: ChatMessage[]): ConsoleUiState {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const action = toQuickReplyYesNoUiAction(message.toolResult)
    if (action) {
      const prompt = extractPrompt(message.toolResult)
      if (action.options.length === 1) {
        return {
          mode: 'single_primary',
          prompt,
          option: mapQuickReplyOption(action.options[0], 'positive'),
        }
      }

      if (action.options.length === 2) {
        const [firstOption, secondOption] = action.options
        const normalizedFirst = normalizeReplyValue(firstOption)
        const normalizedSecond = normalizeReplyValue(secondOption)
        const hasYesNo = [normalizedFirst, normalizedSecond].includes('oui') && [normalizedFirst, normalizedSecond].includes('non')

        if (hasYesNo) {
          const yesSource = normalizedFirst === 'oui' ? firstOption : secondOption
          const noSource = normalizedFirst === 'non' ? firstOption : secondOption

          return {
            mode: 'yes_no',
            prompt,
            yes: mapQuickReplyOption(yesSource, 'positive'),
            no: mapQuickReplyOption(noSource, 'negative'),
          }
        }
      }

      if (action.options.length <= 12) {
        return {
          mode: 'options_grid',
          prompt,
          options: action.options.map((option) => mapQuickReplyOption(option)),
        }
      }

      return {
        mode: 'options_list',
        prompt,
        options: action.options.map((option) => mapQuickReplyOption(option)),
      }
    }

    break
  }

  return { mode: 'text' }
}

export function ChatMinimalPage({ email }: ChatMinimalPageProps) {
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const isDebugUiEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isAssistantTyping, setIsAssistantTyping] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [debugMode, setDebugMode] = useState(false)
  const [headerMessage, setHeaderMessage] = useState<string | null>(null)

  const consoleState = useMemo(() => extractConsoleState(messages), [messages])

  function handleScroll() {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, scrollTop, clientHeight } = scrollRef.current
    const distanceBottom = scrollHeight - (scrollTop + clientHeight)
    setIsNearBottom(distanceBottom < 80)
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

  useEffect(() => {
    if (isNearBottom) {
      scrollToBottom()
    }
  }, [messages.length, isAssistantTyping, isNearBottom])

  async function startConversation() {
    setHeaderMessage(null)
    setMessages([])
    setIsSending(false)
    setIsAssistantTyping(true)
    try {
      const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
      setMessages([
        {
          id: createMessageId(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
        },
      ])
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
    let isMounted = true

    async function loadGreeting() {
      setIsAssistantTyping(true)
      try {
        const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
        if (!isMounted) {
          return
        }

        setMessages([
          {
            id: createMessageId(),
            role: 'assistant',
            content: response.reply,
            createdAt: Date.now(),
            toolResult: response.tool_result,
          },
        ])
      } catch {
        if (!isMounted) {
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
        if (isMounted) {
          setIsAssistantTyping(false)
        }
      }
    }

    void loadGreeting()

    return () => {
      isMounted = false
    }
  }, [])

  async function submitMessage(text: string, displayContent?: string) {
    const trimmed = text.trim()
    if (!trimmed || isSending) {
      return
    }

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: displayContent?.trim().length ? displayContent : trimmed,
      createdAt: Date.now(),
    }

    setMessages((current) => [...current, userMessage])
    setIsSending(true)
    setIsAssistantTyping(true)

    try {
      const response = await sendChatMessage(trimmed, { debug: debugMode })
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
        },
      ])
    } catch (error) {
      const content = error instanceof Error ? error.message : 'Erreur inconnue.'
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

  function handleQuickReply(value: string, label?: string) {
    void submitMessage(value, label ?? value)
  }

  function resetLocalChatState() {
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
              {isDebugUiEnabled && debugMode ? <span className="debug-badge">Debug</span> : null}
            </div>
            <div className="chat-header-actions">
              {isDebugUiEnabled ? (
                <label className="debug-toggle" htmlFor="debug-mode-toggle">
                  <input
                    id="debug-mode-toggle"
                    type="checkbox"
                    checked={debugMode}
                    onChange={(event) => setDebugMode(event.target.checked)}
                  />
                  Debug
                </label>
              ) : null}
              {isDebugUiEnabled && debugMode ? (
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

                return (
                  <div key={message.id} className={message.role === 'user' ? 'msg msg-user' : 'msg msg-assistant'}>
                    {message.content}
                    {isDebugUiEnabled && debugMode && isLastAssistantMessage ? (
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
          </div>

          {!isNearBottom ? (
            <button type="button" className="scroll-down-btn" onClick={scrollToBottom} aria-label="Aller en bas">
              ↓
            </button>
          ) : null}

          <div className="console-area">
            <div className="console-area-inner">
              <ConsolePanel uiState={consoleState} isSending={isSending} onChoose={handleQuickReply} onSubmitText={submitMessage} />
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}
