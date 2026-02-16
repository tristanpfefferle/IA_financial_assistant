import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

import { sendChatMessage } from '../api/agentApi'
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

export function ChatPage({ email }: ChatPageProps) {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [isRefreshingSession, setIsRefreshingSession] = useState(false)
  const debugEnabled = import.meta.env.VITE_UI_DEBUG === 'true'
  const apiBaseUrl = useMemo(() => {
    const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
    return rawBaseUrl.replace(/\/+$/, '')
  }, [])
  const messagesRef = useRef<HTMLElement | null>(null)
  const assistantMessagesCount = useMemo(
    () => messages.filter((chatMessage) => chatMessage.role === 'assistant').length,
    [messages],
  )

  useEffect(() => {
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

  const isConnected = useMemo(() => Boolean(email), [email])
  const canSubmit = message.trim().length > 0 && !isLoading
  const hasUnauthorizedError = useMemo(() => error?.includes('(401)') ?? false, [error])

  useEffect(() => {
    if (!assistantMessagesCount) {
      return
    }

    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: 'smooth' })
  }, [assistantMessagesCount])

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

    const { data, error: refreshError } = await supabase.auth.refreshSession()
    if (refreshError || !data.session?.access_token) {
      setError('Rafraîchissement de session impossible. Veuillez vous déconnecter puis vous reconnecter.')
    }

    setIsRefreshingSession(false)
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
      const response = await sendChatMessage(trimmedMessage)
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

        {debugEnabled ? (
          <div className="debug-banner" role="status" aria-live="polite">
            Connecté: {isConnected ? 'oui' : 'non'} | Email: {email ?? 'inconnu'} | Token: {hasToken ? 'présent' : 'absent'} |
            API: {apiBaseUrl}
          </div>
        ) : null}

        <section className="messages" aria-live="polite" ref={messagesRef}>
          {messages.length === 0 ? <p className="placeholder-text">Commencez la conversation avec l’IA.</p> : null}
          {messages.map((chatMessage) => (
            <article key={chatMessage.id} className={`message message-${chatMessage.role}`}>
              <p className="message-role">{chatMessage.role === 'user' ? 'Vous' : 'Assistant'}</p>
              <p>{chatMessage.content}</p>
              {debugEnabled && chatMessage.role === 'assistant' && chatMessage.toolResult ? (
                <details>
                  <summary>tool_result</summary>
                  <pre>{JSON.stringify(chatMessage.toolResult, null, 2)}</pre>
                </details>
              ) : null}
              {debugEnabled && chatMessage.role === 'assistant' && chatMessage.plan ? (
                <details>
                  <summary>plan</summary>
                  <pre>{JSON.stringify(chatMessage.plan, null, 2)}</pre>
                </details>
              ) : null}
            </article>
          ))}
          {isLoading ? <p className="placeholder-text">Envoi...</p> : null}
        </section>

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
            </button>{' '}
            <button type="button" className="secondary-button" onClick={handleLogout}>
              Se déconnecter
            </button>
          </div>
        ) : null}
      </section>
    </main>
  )
}
