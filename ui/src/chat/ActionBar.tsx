import { useState } from 'react'

type ActionBarProps = {
  onSend: (text: string) => void
  quickReplies?: { id: string; label: string; value: string }[]
  disabled?: boolean
}

export function ActionBar({ onSend, quickReplies, disabled = false }: ActionBarProps) {
  const [value, setValue] = useState('')

  function sendText(text: string) {
    const trimmed = text.trim()
    if (!trimmed || disabled) {
      return
    }
    onSend(trimmed)
    setValue('')
  }

  return (
    <div className="action-bar">
      <div className="quick-replies-row" role="group" aria-label="Réponses rapides">
        {quickReplies?.map((option) => (
          <button
            key={option.id}
            type="button"
            className="console-btn console-btn-neutral"
            disabled={disabled}
            onClick={() => sendText(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <input
        className="input-field"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Écris ton message"
        disabled={disabled}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            sendText(value)
          }
        }}
      />
      <button type="button" className="dock-send-btn" disabled={disabled} onClick={() => sendText(value)} aria-label="Envoyer">
        ➤
      </button>
    </div>
  )
}
