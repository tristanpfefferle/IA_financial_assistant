import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { supabase } from '../lib/supabaseClient'

type AuthMode = 'signin' | 'signup'

type AuthStep = 'mode' | 'email' | 'password' | 'submit'

export function AuthChatPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<AuthMode | null>(null)
  const [step, setStep] = useState<AuthStep>('mode')
  const [emailInput, setEmailInput] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const actionLabel = mode === 'signup' ? 'Créer mon compte' : 'Se connecter'

  const modeMessage = useMemo(() => {
    if (!mode) {
      return null
    }

    return mode === 'signup' ? 'Créer un compte' : 'Se connecter'
  }, [mode])

  function chooseMode(nextMode: AuthMode) {
    setMode(nextMode)
    setStep('email')
    setErrorMessage(null)
    setSuccessMessage(null)
  }

  function submitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalized = emailInput.trim()
    if (!normalized) {
      return
    }

    setEmail(normalized)
    setStep('password')
    setErrorMessage(null)
    setSuccessMessage(null)
  }

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!password) {
      return
    }

    setStep('submit')
    setErrorMessage(null)
    setSuccessMessage(null)
  }

  async function runAuthAction() {
    if (!mode || !email || !password || isLoading) {
      return
    }

    setIsLoading(true)
    setErrorMessage(null)
    setSuccessMessage(null)

    if (mode === 'signup') {
      const { data, error } = await supabase.auth.signUp({ email, password })
      if (error) {
        setErrorMessage('Oups, impossible de créer le compte. Vérifie tes informations.')
        setIsLoading(false)
        return
      }

      if (data.session) {
        navigate('/chat', { replace: true })
        return
      }

      setSuccessMessage('Compte créé ✅ Vérifie tes emails pour confirmer ton compte.')
      setIsLoading(false)
      return
    }

    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) {
      setErrorMessage('Oups, email/mot de passe incorrect.')
      setIsLoading(false)
      return
    }

    navigate('/chat', { replace: true })
  }

  return (
    <main className="chat-layout" aria-label="Connexion">
      <div className="chat-frame">
        <div className="chat-stack auth-chat-stack">
          <div className="auth-title-wrap">
            <h1 className="auth-title">Connexion</h1>
          </div>

          <div className="message-area">
            <div className="chat-scroll auth-chat-scroll">
              <div className="msg msg-assistant">Salut 👋</div>
              <div className="msg msg-assistant">Bienvenue sur Assistant financier.</div>
              <div className="msg msg-assistant">Tu veux te connecter ou créer un compte ?</div>

              {modeMessage ? <div className="msg msg-user msg-short">{modeMessage}</div> : null}

              {step === 'mode' ? (
                <div className="auth-choice-stack" role="group" aria-label="Choix de connexion">
                  <button type="button" className="msg msg-user auth-choice" onClick={() => chooseMode('signin')}>
                    Se connecter
                  </button>
                  <button type="button" className="msg msg-user auth-choice" onClick={() => chooseMode('signup')}>
                    Créer un compte
                  </button>
                </div>
              ) : null}

              {mode ? <div className="msg msg-assistant">Super. Quel est ton email ?</div> : null}
              {step === 'email' ? (
                <div className="msg msg-assistant auth-form-bubble">
                  <form onSubmit={submitEmail} className="auth-chat-form">
                    <input
                      type="email"
                      value={emailInput}
                      onChange={(event) => setEmailInput(event.target.value)}
                      placeholder="ton@email.com"
                      autoComplete="email"
                      required
                    />
                    <button type="submit">Valider l'email</button>
                  </form>
                </div>
              ) : null}

              {email ? <div className="msg msg-user">{email}</div> : null}
              {step === 'password' || step === 'submit' ? (
                <div className="msg msg-assistant">Parfait. Maintenant ton mot de passe.</div>
              ) : null}

              {step === 'password' ? (
                <div className="msg msg-assistant auth-form-bubble">
                  <form onSubmit={submitPassword} className="auth-chat-form">
                    <input
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="Mot de passe"
                      autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                      required
                    />
                    <button type="submit">Valider le mot de passe</button>
                  </form>
                </div>
              ) : null}

              {step === 'submit' ? (
                <div className="auth-submit-wrap">
                  <button type="button" className="msg msg-user auth-submit-button" onClick={() => { void runAuthAction() }} disabled={isLoading}>
                    {isLoading ? 'Chargement...' : actionLabel}
                  </button>
                </div>
              ) : null}

              {errorMessage ? <div className="msg msg-assistant">{errorMessage}</div> : null}
              {successMessage ? <div className="msg msg-assistant">{successMessage}</div> : null}
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}
