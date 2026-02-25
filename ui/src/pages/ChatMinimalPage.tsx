import { useEffect, useMemo, useRef, useState } from 'react'

import { sendChatMessage } from '../api/agentApi'
import { ActionPanel } from '../chat/ActionPanel'
import type { ChatUiState } from '../chat/types'
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

function extractUiState(messages: ChatMessage[]): ChatUiState {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const action = toQuickReplyYesNoUiAction(message.toolResult)
    if (action) {
      return {
        mode: 'quick_replies',
        options: action.options,
      }
    }

    break
  }

  return { mode: 'text' }
}

export function ChatMinimalPage({ email }: ChatMinimalPageProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isAssistantTyping, setIsAssistantTyping] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)

  const uiState = useMemo(() => extractUiState(messages), [messages])

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

  useEffect(() => {
    let isMounted = true

    async function loadGreeting() {
      setIsAssistantTyping(true)
      try {
        const response = await sendChatMessage('', { debug: false, requestGreeting: true })
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
      const response = await sendChatMessage(trimmed, { debug: false })
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

  return (
    <main className="chat-layout" aria-label="Chat minimal">
      <div className="chat-frame">
        <div className="chat-stack">
          <header className="chat-min-header">
            <strong>Assistant financier</strong>
            {email ? <span className="subtle-text">{email}</span> : null}
          </header>

          <div className="message-area">
            <div ref={scrollRef} className="chat-scroll" onScroll={handleScroll}>
              {messages.map((message) => (
                <div key={message.id} className={message.role === 'user' ? 'msg msg-user' : 'msg msg-assistant'}>
                  {message.content}
                </div>
              ))}
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
              <ActionPanel uiState={uiState} isSending={isSending} onQuickReply={handleQuickReply} onSubmitText={submitMessage} />
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}
