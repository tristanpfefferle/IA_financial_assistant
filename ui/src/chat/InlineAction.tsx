import { useMemo, useRef } from 'react'

import {
  toLegacyImportUiRequest,
  toOpenImportPanelUiAction,
  toQuickReplyYesNoUiAction,
} from '../pages/chatUiRequests'

type InlineActionProps = {
  actionState: Record<string, unknown>
  disabled: boolean
  onChoose: (value: string, label?: string) => void
  onImportFile: (file: File) => void
}

type InlineOption = {
  id: string
  label: string
  value: string
  tone: 'positive' | 'negative' | 'neutral' | 'primary'
}

function normalizeAcceptedTypes(value: string[] | undefined): string {
  if (!value) {
    return '.csv'
  }

  const acceptedTypes = value
    .map((item) => item.trim().replace(/^\./, '').toLowerCase())
    .filter((item) => item.length > 0)
    .map((item) => `.${item}`)

  return acceptedTypes.length > 0 ? acceptedTypes.join(',') : '.csv'
}

function toInlineOptions(actionState: Record<string, unknown>): InlineOption[] | null {
  const yesNoAction = toQuickReplyYesNoUiAction(actionState)
  if (yesNoAction) {
    return yesNoAction.options.map((option) => {
      const normalizedValue = option.value.trim().toLowerCase()
      const normalizedLabel = option.label.trim().toLowerCase()
      const isPositive = normalizedValue === 'oui' || normalizedLabel === 'oui' || option.id === 'yes'
      const isNegative = normalizedValue === 'non' || normalizedLabel === 'non' || option.id === 'no'

      return {
        id: option.id,
        label: isPositive ? 'Oui' : isNegative ? 'Non' : option.label,
        value: option.value,
        tone: isPositive ? 'positive' : isNegative ? 'negative' : 'neutral',
      }
    })
  }

  if (actionState.type !== 'ui_action' || typeof actionState.action !== 'string') {
    return null
  }

  const action = actionState.action
  const options = Array.isArray(actionState.options) ? actionState.options : null
  if (!options || (action !== 'single_primary' && action !== 'options_grid' && action !== 'options_list')) {
    return null
  }

  const parsedOptions = options
    .map((option, index): InlineOption | null => {
      if (!option || typeof option !== 'object') {
        return null
      }

      const record = option as Record<string, unknown>
      if (typeof record.label !== 'string' || typeof record.value !== 'string') {
        return null
      }

      return {
        id: typeof record.id === 'string' ? record.id : `${action}-${index}`,
        label: record.label,
        value: record.value,
        tone: action === 'single_primary' ? 'primary' : 'neutral',
      }
    })
    .filter((option): option is InlineOption => option !== null)

  return parsedOptions.length > 0 ? parsedOptions : null
}

export function InlineAction({ actionState, disabled, onChoose, onImportFile }: InlineActionProps) {
  const fileRef = useRef<HTMLInputElement | null>(null)

  const importPanelAction = toOpenImportPanelUiAction(actionState)
  const legacyImportAction = toLegacyImportUiRequest(actionState)
  const acceptedFileTypes = useMemo(
    () => normalizeAcceptedTypes(importPanelAction?.accepted_types ?? legacyImportAction?.accepted_types),
    [importPanelAction?.accepted_types, legacyImportAction?.accepted_types],
  )

  const options = toInlineOptions(actionState)
  if (options && options.length > 0) {
    return (
      <div className="inline-action" role="group" aria-label="Actions">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={`inline-pill inline-pill-${option.tone}`}
            disabled={disabled}
            onClick={() => onChoose(option.value, option.label)}
          >
            {option.label}
          </button>
        ))}
      </div>
    )
  }

  if (importPanelAction || legacyImportAction) {
    return (
      <div className="inline-action" role="group" aria-label="Import de fichier">
        <button
          type="button"
          className="inline-pill inline-pill-primary"
          disabled={disabled}
          onClick={() => fileRef.current?.click()}
        >
          Importer
        </button>
        <input
          ref={fileRef}
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
    )
  }

  return null
}
