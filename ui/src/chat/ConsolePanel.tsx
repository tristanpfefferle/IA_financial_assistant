import { useMemo, useRef } from 'react'

import type { ConsoleOption, ConsoleUiState } from './types'

type ConsolePanelProps = {
  uiState: ConsoleUiState
  isSending: boolean
  onChoose: (value: string, label?: string) => void
  onImportFile: (file: File) => void
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

export function ConsolePanel({ uiState, isSending, onChoose, onImportFile }: ConsolePanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const showPrompt = 'prompt' in uiState && typeof uiState.prompt === 'string' && uiState.prompt.trim().length > 0
  const acceptedFileTypes = useMemo(() => {
    if (uiState.mode !== 'import_file') {
      return '.csv'
    }

    const normalizedTypes = (uiState.acceptedTypes ?? ['csv'])
      .map((item) => item.trim().toLowerCase())
      .filter((item) => item.length > 0)
      .map((item) => (item.startsWith('.') ? item : `.${item}`))

    if (!normalizedTypes.includes('.csv')) {
      normalizedTypes.unshift('.csv')
    }

    return normalizedTypes.join(',')
  }, [uiState])

  return (
    <div className="action-dock" aria-label={`Console panel mode ${uiState.mode}`}>
      <div className="console-panel">
        {uiState.mode !== 'none' ? (
          <div className="action-dock-header">
            <p className="action-dock-title">{showPrompt ? uiState.prompt : 'Étape suivante'}</p>
          </div>
        ) : null}

        {uiState.mode === 'none' ? (
          <p className="dock-idle-text">
            <span aria-hidden="true">ℹ</span>
            <span>À toi de jouer.</span>
          </p>
        ) : null}

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

        {uiState.mode === 'import_file' ? (
          <div className="import-card">
            <p className="import-card-title">
              <span aria-hidden="true">📂</span>
              <span>Relevé bancaire CSV</span>
            </p>
            <button
              type="button"
              className="console-btn console-btn-positive"
              disabled={isSending}
              onClick={() => {
                inputRef.current?.click()
              }}
            >
              {uiState.buttonLabel ?? 'Importer maintenant'}
            </button>
            <input
              ref={inputRef}
              type="file"
              hidden
              accept={acceptedFileTypes}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) {
                  onImportFile(file)
                }
                event.target.value = ''
              }}
            />
            <p className="subtle-text">Ajoute ton CSV pour lancer l’analyse automatique.</p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
