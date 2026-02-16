import { useState, type FormEvent } from 'react'

import { supabase } from '../lib/supabaseClient'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsLoading(true)

    const { error: authError } = await supabase.auth.signInWithPassword({ email, password })

    if (authError) {
      setError(authError.message)
      setIsLoading(false)
      return
    }

    window.location.assign('/')
  }

  return (
    <main className="chat-shell">
      <h1>Connexion</h1>
      <section className="chat-card">
        <form onSubmit={handleSubmit} className="chat-form">
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            required
          />
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Mot de passe"
            required
          />
          <button type="submit" disabled={isLoading}>
            {isLoading ? 'Connexion...' : 'Se connecter'}
          </button>
        </form>

        {error ? <p className="error-text">{error}</p> : null}
      </section>
    </main>
  )
}
