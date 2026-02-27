import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { supabase } from '../lib/supabaseClient'

type AuthMode = 'signin' | 'signup'

function toFriendlyAuthError(message: string): string {
  const normalized = message.toLowerCase()

  if (normalized.includes('invalid login credentials')) {
    return 'Email ou mot de passe incorrect.'
  }

  if (normalized.includes('email') && normalized.includes('invalid')) {
    return "L'adresse email est invalide."
  }

  if (normalized.includes('password') && normalized.includes('at least')) {
    return 'Le mot de passe est trop court.'
  }

  if (normalized.includes('user already registered') || normalized.includes('already been registered')) {
    return 'Un compte existe déjà avec cet email.'
  }

  if (normalized.includes('signup is disabled')) {
    return 'La création de compte est temporairement désactivée.'
  }

  return 'Une erreur est survenue, réessaie.'
}

export function LoginPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<AuthMode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const submitLabel = useMemo(() => {
    if (isLoading) {
      return mode === 'signin' ? 'Connexion…' : 'Création…'
    }

    return mode === 'signin' ? 'Se connecter' : 'Créer un compte'
  }, [isLoading, mode])

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode)
    setError(null)
    setSuccess(null)
  }

  async function runSignUp() {
    if (confirmPassword && password !== confirmPassword) {
      setError('Les mots de passe ne correspondent pas.')
      return
    }

    const { data, error: signUpError } = await supabase.auth.signUp({ email, password })

    if (signUpError) {
      setError(toFriendlyAuthError(signUpError.message))
      return
    }

    if (data.session) {
      navigate('/chat', { replace: true })
      return
    }

    setSuccess('Compte créé ✅ Vérifie tes emails pour confirmer ton compte.')
  }

  async function runSignIn() {
    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password })

    if (signInError) {
      setError(toFriendlyAuthError(signInError.message))
      return
    }

    navigate('/chat', { replace: true })
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSuccess(null)
    setIsLoading(true)

    if (mode === 'signup') {
      await runSignUp()
    } else {
      await runSignIn()
    }

    setIsLoading(false)
  }

  return (
    <main className="auth-page-bg">
      <section className="auth-card-premium" aria-labelledby="auth-title">
        <div className="auth-badge" aria-hidden="true">
          AF
        </div>

        <h1 id="auth-title">Votre assistant financier numérique</h1>
        <p className="auth-intro">Laissez-vous guider dans la gestion de vos finances !</p>

        <div className="auth-segmented" role="tablist" aria-label="Mode d'authentification">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'signin'}
            className={mode === 'signin' ? 'auth-tab is-active' : 'auth-tab'}
            onClick={() => switchMode('signin')}
          >
            Se connecter
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'signup'}
            className={mode === 'signup' ? 'auth-tab is-active' : 'auth-tab'}
            onClick={() => switchMode('signup')}
          >
            Créer un compte
          </button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form-premium">
          <label htmlFor="auth-email">Email</label>
          <input
            id="auth-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            required
          />

          <label htmlFor="auth-password">Mot de passe</label>
          <input
            id="auth-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            required
          />

          {mode === 'signup' ? (
            <>
              <label htmlFor="auth-confirm-password">Confirmer mot de passe</label>
              <input
                id="auth-confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                autoComplete="new-password"
                placeholder="Optionnel"
              />
            </>
          ) : null}

          <button type="submit" className="auth-submit-button" disabled={isLoading}>
            {submitLabel}
          </button>
        </form>

        <p className="auth-switch-hint">
          {mode === 'signin' ? "Pas encore de compte ? " : 'Déjà inscrit ? '}
          <button
            type="button"
            className="auth-switch-link"
            disabled={isLoading}
            onClick={() => switchMode(mode === 'signin' ? 'signup' : 'signin')}
          >
            {mode === 'signin' ? 'Créer un compte' : 'Se connecter'}
          </button>
        </p>

        {error ? (
          <p className="auth-error" role="status" aria-live="polite">
            {error}
          </p>
        ) : null}
        {success ? <p className="subtle-text">{success}</p> : null}
      </section>
    </main>
  )
}
