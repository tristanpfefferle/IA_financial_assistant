import { useEffect, useMemo, useRef } from 'react'

import type { ConsoleOption, ConsoleUiState } from './types'

type ConsolePanelProps = {
  uiState: ConsoleUiState
  isSending: boolean
  selectedOptionId?: string | null
  onSelectOption: (option: ConsoleOption) => void
  onSend: () => void
  canSend: boolean
  sendLabel?: string
  onTriggerImportPicker: () => void
  registerImportPickerTrigger?: (trigger: (() => void) | null) => void
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

function OptionButton({
  option,
  isSending,
  isSelected,
  onSelectOption,
}: {
  option: ConsoleOption
  isSending: boolean
  isSelected: boolean
  onSelectOption: (option: ConsoleOption) => void
}) {
  return (
    <button
      type="button"
      className={`console-btn ${toneClassName(option)}${isSelected ? ' console-btn-selected' : ''}`}
      disabled={isSending || option.disabled}
      aria-disabled={isSending || option.disabled}
      aria-pressed={isSelected}
      onClick={() => onSelectOption(option)}
    >
      {option.label}
    </button>
  )
}

export function ConsolePanel({
  uiState,
  isSending,
  selectedOptionId,
  onSelectOption,
  onSend,
  canSend,
  sendLabel,
  onTriggerImportPicker,
  registerImportPickerTrigger,
  onImportFile,
}: ConsolePanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const triggerImportPicker = () => {
    if (isSending) {
      return
    }
    inputRef.current?.click()
  }

  useEffect(() => {
    if (!registerImportPickerTrigger) {
      return undefined
    }

    registerImportPickerTrigger(() => {
      inputRef.current?.click()
    })
    return () => {
      registerImportPickerTrigger(null)
    }
  }, [registerImportPickerTrigger])

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
      <div className="dock-content console-panel">
        {uiState.mode === 'yes_no' ? (
          <div className="console-split" role="group" aria-label="Réponse oui/non">
            <OptionButton option={uiState.yes} isSending={isSending} isSelected={selectedOptionId === uiState.yes.id} onSelectOption={onSelectOption} />
            <OptionButton option={uiState.no} isSending={isSending} isSelected={selectedOptionId === uiState.no.id} onSelectOption={onSelectOption} />
          </div>
        ) : null}

        {uiState.mode === 'single_primary' ? (
          <OptionButton option={{ ...uiState.option, tone: 'positive' }} isSending={isSending} isSelected={selectedOptionId === uiState.option.id} onSelectOption={onSelectOption} />
        ) : null}

        {uiState.mode === 'options_grid' ? (
          <div className="console-grid" role="group" aria-label="Options">
            {uiState.options.map((option) => (
              <OptionButton key={option.id} option={option} isSending={isSending} isSelected={selectedOptionId === option.id} onSelectOption={onSelectOption} />
            ))}
          </div>
        ) : null}

        {uiState.mode === 'options_list' ? (
          <div className="console-list" role="group" aria-label="Options">
            {uiState.options.map((option) => (
              <OptionButton key={option.id} option={option} isSending={isSending} isSelected={selectedOptionId === option.id} onSelectOption={onSelectOption} />
            ))}
          </div>
        ) : null}

        {uiState.mode === 'import_file' ? (
          <div className="import-card">
            <button
              type="button"
              className="console-btn console-btn-positive"
              disabled={isSending}
              onClick={triggerImportPicker}
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
          </div>
        ) : null}
      </div>
      <div className="dock-footer">
        <button
          type="button"
          className="dock-send-btn"
          disabled={!canSend || isSending}
          onClick={uiState.mode === 'import_file' ? onTriggerImportPicker : onSend}
          aria-label={sendLabel ?? 'Envoyer'}
        >
          ➤
        </button>
      </div>
    </div>
  )
}
