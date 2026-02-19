import { describe, expect, it, vi } from 'vitest'

import { installSessionResetOnPageExit, logoutWithSessionReset } from './sessionLifecycle'

describe('logoutWithSessionReset', () => {
  it('calls resetSession before signOut', async () => {
    const calls: string[] = []
    const onLogoutError = vi.fn()

    await logoutWithSessionReset({
      resetSession: async () => {
        calls.push('reset')
      },
      signOut: async () => {
        calls.push('signout')
        return { error: null }
      },
      onLogoutError,
    })

    expect(calls).toEqual(['reset', 'signout'])
    expect(onLogoutError).not.toHaveBeenCalled()
  })

  it('continues logout flow when reset fails', async () => {
    const signOut = vi.fn(async () => ({ error: null }))
    const onLogoutError = vi.fn()

    await logoutWithSessionReset({
      resetSession: async () => {
        throw new Error('network error')
      },
      signOut,
      onLogoutError,
    })

    expect(signOut).toHaveBeenCalledTimes(1)
    expect(onLogoutError).not.toHaveBeenCalled()
  })
})

describe('installSessionResetOnPageExit', () => {
  it('wires pagehide handler and cleanup', () => {
    const reset = vi.fn()

    const cleanup = installSessionResetOnPageExit(reset)

    window.dispatchEvent(new Event('pagehide'))
    expect(reset).toHaveBeenCalledTimes(1)

    cleanup()
    window.dispatchEvent(new Event('pagehide'))
    expect(reset).toHaveBeenCalledTimes(1)
  })
})
