export function installSessionResetOnPageExit(reset: () => void): () => void {
  const handlePageHide = (event: PageTransitionEvent) => {
    if (event.persisted) {
      return
    }

    reset()
  }

  window.addEventListener('pagehide', handlePageHide)

  return () => {
    window.removeEventListener('pagehide', handlePageHide)
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
