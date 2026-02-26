import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import './App.css'
import { supabase } from './lib/supabaseClient'
import { ChatMinimalPage } from './pages/ChatMinimalPage'
import { LoginPage } from './pages/LoginPage'

function ProtectedRoute({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [isLoading, setIsLoading] = useState(true)
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  useEffect(() => {
    let isMounted = true

    async function checkSession() {
      const { data } = await supabase.auth.getSession()
      if (!isMounted) {
        return
      }

      const hasSession = Boolean(data.session)
      setIsAuthenticated(hasSession)
      if (!hasSession) {
        navigate('/login', { replace: true })
      }
      setIsLoading(false)
    }

    void checkSession()

    return () => {
      isMounted = false
    }
  }, [navigate])

  if (isLoading) {
    return null
  }

  if (!isAuthenticated) {
    return null
  }

  return <>{children}</>
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/chat"
        element={
          <ProtectedRoute>
            <ChatMinimalPage />
          </ProtectedRoute>
        }
      />
      <Route path="/" element={<Navigate to="/chat" replace />} />
    </Routes>
  )
}

export default App
