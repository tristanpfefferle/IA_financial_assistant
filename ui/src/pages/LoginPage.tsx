import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { supabase } from '../lib/supabaseClient'

export function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function runAuthAction(action: 'signin' | 'signup') {
    setError(null)
    setSuccess(null)
    setIsLoading(true)

    if (action === 'signup') {
      const { data, error: signUpError } = await supabase.auth.signUp({ email, password })

      if (signUpError) {
        setError(signUpError.message)
      } else if (data.session) {
        navigate('/chat', { replace: true })
      } else {
        setSuccess('Compte créé ✅ Vérifie tes emails pour confirmer ton compte.')
      }

      setIsLoading(false)
      return
    }

    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password })

    if (signInError) {
      setError(signInError.message)
    } else {
      navigate('/chat', { replace: true })
    }

    setIsLoading(false)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void runAuthAction('signin')
  }

  return (
    <main className="chat-shell">
      <section className="chat-card auth-card">
        <h1>Connexion</h1>
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
            autoComplete="current-password"
            required
          />

          <div className="message-actions">
            <button type="submit" disabled={isLoading}>
              {isLoading ? 'Connexion...' : 'Se connecter'}
            </button>
            <button
              type="button"
              className="secondary-button"
              disabled={isLoading}
              onClick={() => {
                void runAuthAction('signup')
              }}
            >
              {isLoading ? 'Création...' : 'Créer un compte'}
            </button>
          </div>
        </form>

        {error ? <p className="error-text">{error}</p> : null}
        {success ? <p className="subtle-text">{success}</p> : null}
      </section>
    </main>
  )
}
