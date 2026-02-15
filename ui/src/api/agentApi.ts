export type AgentChatResponse = {
  reply: string
  tool_result: Record<string, unknown> | null
}

export async function sendChatMessage(message: string): Promise<AgentChatResponse> {
  const response = await fetch('http://127.0.0.1:8000/agent/chat', {
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
