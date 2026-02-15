import { FormEvent, useState } from 'react'
import { postUserMessage } from '../api/agentApi'

type Message = { role: 'user' | 'assistant'; content: string }

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: 'Assistant financier IA prêt (placeholder).' },
  ])
  const [input, setInput] = useState('')

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!input.trim()) return

    const userMessage = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }])

    const reply = await postUserMessage(userMessage)
    setMessages((prev) => [...prev, { role: 'assistant', content: reply }])
  }

  return (
    <main style={{ maxWidth: 760, margin: '0 auto', padding: 24, fontFamily: 'sans-serif' }}>
      <h1>IA Financial Assistant — Chat</h1>
      <p>UI minimale de debug (aucune logique métier).</p>
      <section style={{ border: '1px solid #ddd', borderRadius: 8, padding: 16, minHeight: 220 }}>
        {messages.map((message, idx) => (
          <p key={idx}>
            <strong>{message.role}:</strong> {message.content}
          </p>
        ))}
      </section>
      <form onSubmit={onSubmit} style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Pose ta question financière..."
          style={{ flex: 1, padding: 8 }}
        />
        <button type="submit">Envoyer</button>
      </form>
    </main>
  )
}
