import { useState, type FormEvent } from 'react'

import { sendChatMessage, type AgentChatResponse } from '../api/agentApi'

type TransactionItem = {
  id: string
  description: string
  booked_at: string
  amount: {
    amount: string
    currency: string
  }
}

function isTransactionSearchResult(toolResult: Record<string, unknown>): toolResult is { items: TransactionItem[] } {
  return Array.isArray(toolResult.items)
}

export function ChatPage() {
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<AgentChatResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!message.trim()) {
      return
    }

    setIsLoading(true)
    setError(null)

    try {
      const response = await sendChatMessage(message)
      setResult(response)
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : 'Erreur inconnue'
      setError(errorMessage)
      setResult(null)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="chat-shell">
      <h1>Assistant financier IA (debug)</h1>
      <section className="chat-card">
        <form onSubmit={handleSubmit} className="chat-form">
          <input
            type="text"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="search: coffee from:2025-01-01 to:2025-01-31"
            aria-label="Message"
          />
          <button type="submit" disabled={isLoading}>
            {isLoading ? 'Envoi...' : 'Envoyer'}
          </button>
        </form>

        <p className="help-text">
          Exemples: <code>search: coffee account:acc_main</code>, <code>search: coffee from:2025-01-01 to:2025-01-31</code>,{' '}
          <code>search: min:-100 max:0 limit:10 offset:0</code>
        </p>

        {error ? <p className="error-text">{error}</p> : null}

        {result ? (
          <article className="chat-result" aria-label="agent-result">
            <p>
              <strong>Reply:</strong> {result.reply}
            </p>

            {result.tool_result ? (
              <>
                <h2>Tool result JSON</h2>
                <pre>
                  <code>{JSON.stringify(result.tool_result, null, 2)}</code>
                </pre>

                {isTransactionSearchResult(result.tool_result) ? (
                  <>
                    <h2>Transactions</h2>
                    <ul>
                      {result.tool_result.items.map((item) => (
                        <li key={item.id}>
                          {item.description} — {item.amount.amount} {item.amount.currency} — {item.booked_at}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </>
            ) : (
              <p>Pas de tool_result.</p>
            )}
          </article>
        ) : null}
      </section>
    </main>
  )
}
