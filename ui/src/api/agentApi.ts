import { supabase } from '../lib/supabaseClient'

export type AgentChatResponse = {
  reply: string
  tool_result: Record<string, unknown> | null
  plan: Record<string, unknown> | null
}

export async function sendChatMessage(message: string): Promise<AgentChatResponse> {
  const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
  const baseUrl = rawBaseUrl.replace(/\/+$/, '')

  const { data } = await supabase.auth.getSession()
  const accessToken = data.session?.access_token

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  const response = await fetch(`${baseUrl}/agent/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message }),
  })

  if (!response.ok) {
    throw new Error(`Erreur API agent (${response.status})`)
  }

  return (await response.json()) as AgentChatResponse
}
