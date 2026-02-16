import { supabase } from '../lib/supabaseClient'

export type AgentChatResponse = {
  reply: string
  tool_result: Record<string, unknown> | null
  plan: Record<string, unknown> | null
}

type ErrorPayload = {
  detail?: string
}

async function getAccessToken(): Promise<string | null> {
  const { data: sessionData } = await supabase.auth.getSession()
  if (sessionData.session?.access_token) {
    return sessionData.session.access_token
  }

  const { data: refreshedData, error } = await supabase.auth.refreshSession()
  if (error) {
    return null
  }

  return refreshedData.session?.access_token ?? null
}

async function extractErrorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as ErrorPayload
    if (payload.detail) {
      return payload.detail
    }
  } catch {
    // Empty body or non-JSON body.
  }

  return response.statusText || 'Erreur inconnue'
}

export async function sendChatMessage(message: string): Promise<AgentChatResponse> {
  const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
  const baseUrl = rawBaseUrl.replace(/\/+$/, '')
  const accessToken = await getAccessToken()

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
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API agent (${response.status}): ${detail}`)
  }

  return (await response.json()) as AgentChatResponse
}
