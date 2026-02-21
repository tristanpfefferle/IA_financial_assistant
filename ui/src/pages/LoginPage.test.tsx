import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LoginPage } from './LoginPage'

const { signInWithPassword, signUp } = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
}))

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      signInWithPassword,
      signUp,
    },
  },
}))

describe('LoginPage', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    signInWithPassword.mockReset()
    signUp.mockReset()
    signInWithPassword.mockResolvedValue({ error: null })
    signUp.mockResolvedValue({ data: { session: null }, error: null })
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('creates account via supabase signUp and renders success message', async () => {
    await act(async () => {
      createRoot(container).render(<LoginPage />)
    })

    const createButton = Array.from(container.querySelectorAll('button')).find((btn) => btn.textContent?.includes('Créer un compte'))
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const [emailInput, passwordInput] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      valueSetter?.call(emailInput, 'new@example.com')
      emailInput.dispatchEvent(new Event('input', { bubbles: true }))
      valueSetter?.call(passwordInput, 'super-secret')
      passwordInput.dispatchEvent(new Event('input', { bubbles: true }))
    })

    const form = container.querySelector('form') as HTMLFormElement
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(signUp).toHaveBeenCalledWith({ email: 'new@example.com', password: 'super-secret' })
    expect(container.textContent).toContain('Compte créé ✅ Vérifie tes emails pour confirmer ton compte.')
  })
})
