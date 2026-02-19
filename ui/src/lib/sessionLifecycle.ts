export function installSessionResetOnPageExit(reset: () => void): () => void {
  const handlePageHide = () => {
    reset()
  }

  const handleVisibilityChange = () => {
    if (document.visibilityState !== 'hidden') {
      return
    }

    reset()
  }

  window.addEventListener('pagehide', handlePageHide)
  document.addEventListener('visibilitychange', handleVisibilityChange)

  return () => {
    window.removeEventListener('pagehide', handlePageHide)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }
}

type LogoutDependencies = {
  resetSession: () => Promise<void>
  signOut: () => Promise<{ error: Error | null }>
  onLogoutError: () => void
}

export async function logoutWithSessionReset({ resetSession, signOut, onLogoutError }: LogoutDependencies): Promise<void> {
  try {
    await resetSession()
  } catch {
    // Best-effort reset should never block logout.
  }

  try {
    const { error } = await signOut()
    if (error) {
      throw error
    }
  } catch {
    onLogoutError()
  }
}
