import { useState } from 'react'

import { resetSession } from '../api/agentApi'
import { logoutWithSessionReset } from '../lib/sessionLifecycle'
import { supabase } from '../lib/supabaseClient'

type ChatPageProps = {
  email?: string
}

export function ChatPage({ email }: ChatPageProps) {
  const [logoutError, setLogoutError] = useState<string | null>(null)
  const [resetFeedback, setResetFeedback] = useState<string | null>(null)
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [isResetting, setIsResetting] = useState(false)

  async function handleLogout() {
    setLogoutError(null)
    setIsLoggingOut(true)

    await logoutWithSessionReset({
      resetSession: () => resetSession({ timeoutMs: 1500 }),
      signOut: () => supabase.auth.signOut(),
      onLogoutError: () => {
        setLogoutError('Impossible de se déconnecter pour le moment.')
      },
    })

    setIsLoggingOut(false)
  }

  async function handleResetForTests() {
    setResetFeedback(null)
    setIsResetting(true)

    try {
      await resetSession({ timeoutMs: 1500 })
      setResetFeedback('Session agent réinitialisée.')
    } finally {
      setIsResetting(false)
    }
  }

  return (
    <main className="chat-shell">
      <section className="card chat-card" aria-label="chat-placeholder">
        <h1>Chat désactivé (refonte en cours)</h1>
        <p className="subtle-text">Cette zone est temporairement simplifiée pour stabiliser l&apos;interface.</p>
        <p className="subtle-text">Connecté en tant que {email ?? 'utilisateur inconnu'}.</p>

        <div className="message-actions" style={{ marginTop: '1rem' }}>
          <button type="button" onClick={handleLogout} disabled={isLoggingOut}>
            {isLoggingOut ? 'Déconnexion...' : 'Se déconnecter'}
          </button>
          <button type="button" className="secondary-button" onClick={handleResetForTests} disabled={isResetting}>
            {isResetting ? 'Reset...' : 'Reset (tests)'}
          </button>
        </div>

        {logoutError ? <p className="error-text">{logoutError}</p> : null}
        {resetFeedback ? <p className="subtle-text">{resetFeedback}</p> : null}
      </section>
    </main>
  )
}
