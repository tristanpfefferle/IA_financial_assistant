import { useMemo, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { Virtuoso } from 'react-virtuoso'

import { resetSession, resolveApiBaseUrl, sendChatMessage } from '../api/agentApi'
import { logoutWithSessionReset } from '../lib/sessionLifecycle'
import { supabase } from '../lib/supabaseClient'

type ChatPageProps = {
  email?: string
}

type ChatRole = 'user' | 'assistant'

type ChatMessage = {
  id: string
  role: ChatRole
  content: string
  createdAt: Date
}

function splitAssistantReply(reply: string): string[] {
  const cleanedReply = reply.trim()
  if (!cleanedReply) {
    return ['Je n\'ai pas de réponse pour le moment.']
  }

  return cleanedReply
    .split(/\n{2,}/)
    .map((segment) => segment.trim())
    .filter(Boolean)
}

function formatMessageTime(value: Date): string {
  return new Intl.DateTimeFormat('fr-CH', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(value)
}

function createMessage(role: ChatRole, content: string): ChatMessage {
  return {
    id: `${role}-${crypto.randomUUID()}`,
    role,
    content,
    createdAt: new Date(),
  }
}

export function ChatPage({ email }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apiBaseUrl = useMemo(() => resolveApiBaseUrl(), [])

  async function handleLogout() {
    setError(null)
    setIsLoggingOut(true)

    await logoutWithSessionReset({
      resetSession: () => resetSession({ timeoutMs: 1500 }),
      signOut: () => supabase.auth.signOut(),
      onLogoutError: () => {
        setError('Impossible de se déconnecter pour le moment.')
      },
    })

    setIsLoggingOut(false)
  }

  function enqueueAssistantMessages(replySegments: string[]) {
    if (replySegments.length === 0) {
      return
    }

    const assistantMessages = replySegments.map((segment) => createMessage('assistant', segment))
    setMessages((previous) => [...previous, ...assistantMessages])
  }

  function drainAssistantQueue(reply: string) {
    const assistantQueue = splitAssistantReply(reply)
    enqueueAssistantMessages(assistantQueue)
  }

  async function submitForm(message: string) {
    setMessages((previous) => [...previous, createMessage('user', message)])

    const response = await sendChatMessage(message)
    drainAssistantQueue(response.reply)
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault()

    const message = draft.trim()
    if (!message || isSubmitting) {
      return
    }

    setDraft('')
    setError(null)
    setIsSubmitting(true)
    setIsTyping(true)

    try {
      await submitForm(message)
    } catch (submitError) {
      const errorMessage = submitError instanceof Error ? submitError.message : 'Erreur inconnue'
      setError(errorMessage)
    } finally {
      setIsSubmitting(false)
      setIsTyping(false)
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSubmit()
    }
  }

  return (
    <main className="chat-layout chat-layout-professional">
      <aside className="sidebar">
        <section className="card sidebar-card">
          <h2>Espace IA</h2>
          <p className="subtle-text">Connecté: {email ?? 'utilisateur inconnu'}</p>
          <p className="subtle-text">API: {apiBaseUrl}</p>
          <button type="button" onClick={handleLogout} disabled={isLoggingOut}>
            {isLoggingOut ? 'Déconnexion...' : 'Se déconnecter'}
          </button>
        </section>
      </aside>

      <section className="card chat-panel chat-panel-pro" aria-label="chat-professional">
        <header className="chat-header">
          <h1>Assistant financier</h1>
        </header>

        <div className="messages-viewport">
          <Virtuoso
            className="messages messages-list"
            data={messages}
            atBottomStateChange={setIsAtBottom}
            followOutput={isAtBottom ? 'smooth' : false}
            components={{
              Footer: () => <div style={{ height: 24 }} aria-hidden="true" />,
            }}
            itemContent={(_index, item) => {
              const roleLabel = item.role === 'user' ? 'Vous' : 'Assistant'

              return (
                <div className={`message-row ${item.role === 'user' ? 'message-row-user' : 'message-row-assistant'}`}>
                  <article
                    className={`message ${item.role === 'user' ? 'message-user' : 'message-assistant'}`}
                    data-testid={`message-bubble-${item.role}`}
                  >
                    <p className="message-content">{item.content}</p>
                    <p className="message-time">
                      {roleLabel} · {formatMessageTime(item.createdAt)}
                    </p>
                  </article>
                </div>
              )
            }}
          />
        </div>

        <div className="composer-area sticky-bottom">
          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Posez votre question financière..."
              aria-label="Message"
              rows={1}
            />
            <button
              type="submit"
              className="send-icon-button"
              aria-label="Envoyer"
              disabled={isSubmitting || !draft.trim()}
            >
              ➤
            </button>
          </form>
          {isTyping ? <p className="subtle-text typing-indicator">Assistant en train d&apos;écrire…</p> : null}
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      </section>
    </main>
  )
}
