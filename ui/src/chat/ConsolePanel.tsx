import type { ConsoleOption, ConsoleUiState } from './types'

type ConsolePanelProps = {
  uiState: ConsoleUiState
  isSending: boolean
  onChoose: (value: string, label?: string) => void
}

function toneClassName(option: ConsoleOption): string {
  if (option.tone === 'positive') {
    return 'console-btn-positive'
  }
  if (option.tone === 'negative') {
    return 'console-btn-negative'
  }
  return 'console-btn-neutral'
}

function OptionButton({ option, isSending, onChoose }: { option: ConsoleOption; isSending: boolean; onChoose: (value: string, label?: string) => void }) {
  return (
    <button
      type="button"
      className={`console-btn ${toneClassName(option)}`}
      disabled={isSending || option.disabled}
      aria-disabled={isSending || option.disabled}
      onClick={() => onChoose(option.value, option.label)}
    >
      {option.label}
    </button>
  )
}

export function ConsolePanel({ uiState, isSending, onChoose }: ConsolePanelProps) {
  const showPrompt = 'prompt' in uiState && typeof uiState.prompt === 'string' && uiState.prompt.trim().length > 0

  return (
    <div className="console-panel" aria-label={`Console panel mode ${uiState.mode}`}>
      {showPrompt ? <p className="subtle-text">{uiState.prompt}</p> : null}

      {uiState.mode === 'none' ? <p className="subtle-text">Choisis une option ci-dessous pour continuer.</p> : null}

      {uiState.mode === 'yes_no' ? (
        <div className="console-split" role="group" aria-label="Réponse oui/non">
          <OptionButton option={uiState.yes} isSending={isSending} onChoose={onChoose} />
          <OptionButton option={uiState.no} isSending={isSending} onChoose={onChoose} />
        </div>
      ) : null}

      {uiState.mode === 'single_primary' ? (
        <OptionButton option={{ ...uiState.option, tone: 'positive' }} isSending={isSending} onChoose={onChoose} />
      ) : null}

      {uiState.mode === 'options_grid' ? (
        <div className="console-grid" role="group" aria-label="Options">
          {uiState.options.map((option) => (
            <OptionButton key={option.id} option={option} isSending={isSending} onChoose={onChoose} />
          ))}
        </div>
      ) : null}

      {uiState.mode === 'options_list' ? (
        <div className="console-list" role="group" aria-label="Options">
          {uiState.options.map((option) => (
            <OptionButton key={option.id} option={option} isSending={isSending} onChoose={onChoose} />
          ))}
        </div>
      ) : null}
    </div>
  )
}
