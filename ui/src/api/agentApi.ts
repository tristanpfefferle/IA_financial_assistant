export async function postUserMessage(message: string): Promise<string> {
  const response = await fetch('http://localhost:8000/agent/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })

  if (!response.ok) {
    return '[mock] Agent indisponible en local.'
  }

  const payload = (await response.json()) as { reply?: string }
  return payload.reply ?? '[mock] Réponse vide.'
}
