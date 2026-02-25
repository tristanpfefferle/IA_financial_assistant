import { useState, type FormEvent } from 'react'

import type { ChatUiState } from './types'

type ActionPanelProps = {
  uiState: ChatUiState
  isSending: boolean
  onQuickReply: (value: string, label?: string) => void
  onSubmitText: (text: string) => void
}

export function ActionPanel({ uiState, isSending, onQuickReply, onSubmitText }: ActionPanelProps) {
  const [text, setText] = useState('')

  const showQuickReplies = uiState.mode === 'quick_replies' || uiState.mode === 'quick_replies_text'
  const showText = uiState.mode === 'text' || uiState.mode === 'quick_replies_text'

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedText = text.trim()
    if (!trimmedText || isSending) {
      return
    }

    onSubmitText(trimmedText)
    setText('')
  }

  return (
    <div className="action-panel" aria-label={`Action panel mode ${uiState.mode}`}>
      {showQuickReplies ? (
        <div>
          {uiState.prompt ? <p className="subtle-text">{uiState.prompt}</p> : null}
          <div className="quick-replies-row" role="group" aria-label="Quick replies">
            {uiState.options.map((option) => (
              <button
                key={option.id}
                type="button"
                className="quick-reply-chip"
                disabled={isSending || option.disabled}
                aria-disabled={isSending || option.disabled}
                onClick={() => onQuickReply(option.value, option.label)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {showText ? (
        <form onSubmit={handleSubmit} className="composer">
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={uiState.placeholder ?? 'Pose une question sur tes finances…'}
            aria-label="Message"
            disabled={isSending}
          />
          <button type="submit" disabled={isSending || text.trim().length === 0}>
            {uiState.submitLabel ?? 'Envoyer'}
          </button>
        </form>
      ) : null}
    </div>
  )
}
