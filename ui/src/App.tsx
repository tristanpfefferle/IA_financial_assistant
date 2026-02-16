import { useEffect, useState } from 'react'
import type { Session } from '@supabase/supabase-js'

import './App.css'
import { supabase } from './lib/supabaseClient'
import { ChatPage } from './pages/ChatPage'
import { LoginPage } from './pages/LoginPage'

function App() {
  const [session, setSession] = useState<Session | null>(null)

  useEffect(() => {
    let isMounted = true

    supabase.auth.getSession().then(({ data }) => {
      if (isMounted) {
        setSession(data.session)
      }
    })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
    })

    return () => {
      isMounted = false
      subscription.unsubscribe()
    }
  }, [])

  if (!session) {
    return <LoginPage />
  }

  return <ChatPage email={session.user.email} />
}

export default App
