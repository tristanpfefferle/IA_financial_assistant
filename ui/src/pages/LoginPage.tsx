import { useState, type FormEvent } from 'react'

import { supabase } from '../lib/supabaseClient'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [authMode, setAuthMode] = useState<'signin' | 'signup'>('signin')

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSuccess(null)
    setIsLoading(true)

    if (authMode === 'signup') {
      const { data, error: signUpError } = await supabase.auth.signUp({ email, password })

      if (signUpError) {
        setError(signUpError.message)
      } else if (data.session) {
        setSuccess('Compte créé et connecté ✅')
      } else {
        setSuccess('Compte créé ✅ Vérifie tes emails pour confirmer ton compte.')
      }

      setIsLoading(false)
      return
    }

    const { error: authError } = await supabase.auth.signInWithPassword({ email, password })

    if (authError) {
      setError(authError.message)
    }

    setIsLoading(false)
  }

  return (
    <main className="chat-shell">
      <section className="chat-card auth-card">
        <h1>{authMode === 'signin' ? 'Connexion' : 'Créer un compte'}</h1>
        <div className="message-actions" role="tablist" aria-label="Mode authentification">
          <button
            type="button"
            className={authMode === 'signin' ? undefined : 'secondary-button'}
            onClick={() => {
              setAuthMode('signin')
              setError(null)
              setSuccess(null)
            }}
          >
            Se connecter
          </button>
          <button
            type="button"
            className={authMode === 'signup' ? undefined : 'secondary-button'}
            onClick={() => {
              setAuthMode('signup')
              setError(null)
              setSuccess(null)
            }}
          >
            Créer un compte
          </button>
        </div>
        <form onSubmit={handleSubmit} className="chat-form auth-form">
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            autoComplete="email"
            required
          />
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Mot de passe"
            autoComplete={authMode === 'signin' ? 'current-password' : 'new-password'}
            required
          />
          <button type="submit" disabled={isLoading}>
            {isLoading ? (authMode === 'signin' ? 'Connexion...' : 'Création...') : authMode === 'signin' ? 'Se connecter' : 'Créer mon compte'}
          </button>
        </form>

        {error ? <p className="error-text">{error}</p> : null}
        {success ? <p className="subtle-text">{success}</p> : null}
      </section>
    </main>
  )
}
