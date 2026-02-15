export type AgentChatResponse = {
  reply: string
  tool_result: Record<string, unknown> | null
  plan: Record<string, unknown> | null
}

export async function sendChatMessage(message: string): Promise<AgentChatResponse> {
  const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
  const baseUrl = rawBaseUrl.replace(/\/+$/, '')

  const response = await fetch(`${baseUrl}/agent/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ message }),
  })

  if (!response.ok) {
    throw new Error(`Erreur API agent (${response.status})`)
  }

  return (await response.json()) as AgentChatResponse
}
