import { useMemo, useRef, useState } from 'react'

import { buildFormSubmitPayload } from './formSubmit'
import {
  toFormUiAction,
  toLegacyImportUiRequest,
  toAnyPdfUiRequest,
  toOpenImportPanelUiAction,
  toQuickReplyYesNoUiAction,
} from '../pages/chatUiRequests'
import { supabase } from '../lib/supabaseClient'

type InteractiveCardProps = {
  toolResult: Record<string, unknown>
  onSubmit: (payload: { message: string; humanText?: string }) => void
  onImport: (file: File) => void
}

export function ChatInteractiveCard({ toolResult, onSubmit, onImport }: InteractiveCardProps) {
  const quickRepliesAction = toQuickReplyYesNoUiAction(toolResult)
  const formAction = toFormUiAction(toolResult)
  const openImportPanel = toOpenImportPanelUiAction(toolResult)
  const openPdfAction = toAnyPdfUiRequest(toolResult)
  const legacyImportRequest = toLegacyImportUiRequest(toolResult)

  const [values, setValues] = useState<Record<string, string>>(() => {
    if (!formAction) {
      return {}
    }
    const initialValues: Record<string, string> = {}
    for (const field of formAction.fields) {
      initialValues[field.id] = field.value ?? field.default_value ?? ''
    }
    return initialValues
  })

  const [selectedMultiValues, setSelectedMultiValues] = useState<Record<string, Set<string>>>(() => {
    if (!formAction) {
      return {}
    }
    const initialSelected: Record<string, Set<string>> = {}
    for (const field of formAction.fields) {
      if (field.type !== 'multi_select' && field.type !== 'multi-select') {
        continue
      }
      const rawSelection = field.value ?? field.default_value ?? ''
      initialSelected[field.id] = new Set(
        rawSelection
          .split(',')
          .map((item) => item.trim())
          .filter((item) => item.length > 0),
      )
    }
    return initialSelected
  })

  const fileRef = useRef<HTMLInputElement | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [pdfErrorMessage, setPdfErrorMessage] = useState<string | null>(null)

  async function handleOpenPdf(url: string) {
    setPdfErrorMessage(null)

    try {
      const { data } = await supabase.auth.getSession()
      const accessToken = data.session?.access_token
      if (!accessToken) {
        console.warn('No Supabase session found while opening PDF, falling back to window.open(url).')
        window.open(url, '_blank', 'noopener,noreferrer')
        return
      }

      const response = await fetch(url, {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      })

      if (!response.ok) {
        throw new Error(`Impossible d'ouvrir le rapport (HTTP ${response.status})`)
      }

      const blob = await response.blob()
      const blobUrl = URL.createObjectURL(blob)
      window.open(blobUrl, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => {
        URL.revokeObjectURL(blobUrl)
      }, 60_000)
    } catch (error) {
      console.error('Failed to open PDF report with authenticated fetch', error)
      setPdfErrorMessage('Impossible d’ouvrir le PDF pour le moment. Réessaie dans quelques instants.')
    }
  }

  const acceptedFileTypes = useMemo(() => {
    const acceptedTypes = openImportPanel?.accepted_types ?? legacyImportRequest?.accepted_types ?? ['csv']
    return acceptedTypes
      .map((item) => item.trim().replace(/^\./, '').toLowerCase())
      .filter((item) => item.length > 0)
      .map((item) => `.${item}`)
      .join(',')
  }, [legacyImportRequest?.accepted_types, openImportPanel?.accepted_types])

  if (quickRepliesAction) {
    return (
      <div className="chat-card">
        <div className="quick-replies-row" role="group" aria-label="Actions rapides">
          {quickRepliesAction.options.map((option) => (
            <button key={option.id} type="button" className="console-btn console-btn-neutral" onClick={() => onSubmit({ message: option.value })}>
              {option.label}
            </button>
          ))}
        </div>
      </div>
    )
  }

  if (formAction) {
    const isProfileUpdateForm = String(formAction.form_id) === 'profile_update'
    const sortedFields = isProfileUpdateForm
      ? [
          ...formAction.fields.filter((field) => field.id === 'first_name' || field.id === 'last_name'),
          ...formAction.fields.filter((field) => field.id !== 'first_name' && field.id !== 'last_name'),
        ]
      : formAction.fields

    return (
      <form
        className="chat-card"
        onSubmit={(event) => {
          event.preventDefault()
          const submitValues: Record<string, string | string[]> = { ...values }
          for (const field of formAction.fields) {
            if (field.type !== 'multi_select' && field.type !== 'multi-select') {
              continue
            }
            submitValues[field.id] = Array.from(selectedMultiValues[field.id] ?? new Set<string>())
          }
          const payload = buildFormSubmitPayload(formAction, submitValues)
          onSubmit({ message: payload.messageToBackend, humanText: payload.humanText })
        }}
      >
        <div className={`form-fields${isProfileUpdateForm ? ' compact' : ''}`}>
          {sortedFields.map((field) => (
            <div key={field.id} className="form-field">
              <span>{field.label}</span>
              {field.type === 'multi_select' || field.type === 'multi-select' ? (
                <div className="form-multi-select-grid">
                  {field.options?.map((option) => {
                    const selected = selectedMultiValues[field.id]?.has(option.value) ?? false
                    return (
                      <label key={option.id ?? `${field.id}-${option.value}`} className="form-multi-select-option">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={(event) => {
                            setSelectedMultiValues((current) => {
                              const nextSet = new Set(current[field.id] ?? [])
                              if (event.target.checked) {
                                nextSet.add(option.value)
                              } else {
                                nextSet.delete(option.value)
                              }
                              return {
                                ...current,
                                [field.id]: nextSet,
                              }
                            })
                          }}
                        />
                        <span>{option.label}</span>
                      </label>
                    )
                  })}
                </div>
              ) : (
                <input
                  id={`form-field-${field.id}`}
                  type={field.type === 'date' ? 'date' : field.type}
                  value={values[field.id] ?? ''}
                  required={field.required}
                  placeholder={field.placeholder}
                  onChange={(event) => {
                    const nextValue = event.target.value
                    setValues((current) => ({
                      ...current,
                      [field.id]: nextValue,
                    }))
                  }}
                />
              )}
            </div>
          ))}
        </div>
        <div className="dock-footer">
          <button type="submit" className="dock-send-btn" aria-label={formAction.submit_label || 'Envoyer'}>
            ➤
          </button>
        </div>
      </form>
    )
  }

  if (openPdfAction) {
    return (
      <div className="chat-card">
        <button
          type="button"
          className="console-btn console-btn-neutral"
          onClick={() => {
            void handleOpenPdf(openPdfAction.url)
          }}
          aria-label={openPdfAction.title}
        >
          📄 {openPdfAction.label}
        </button>
        {pdfErrorMessage ? <p className="subtle-text">{pdfErrorMessage}</p> : null}
      </div>
    )
  }

  if (openImportPanel || legacyImportRequest) {
    return (
      <div className="chat-card import-card">
        <div
          className={`dropzone${isDragOver ? ' is-dragover' : ''}`}
          role="button"
          tabIndex={0}
          onClick={() => fileRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              fileRef.current?.click()
            }
          }}
          onDragOver={(event) => {
            event.preventDefault()
            setIsDragOver(true)
          }}
          onDragLeave={() => {
            setIsDragOver(false)
          }}
          onDrop={(event) => {
            event.preventDefault()
            setIsDragOver(false)
            const file = event.dataTransfer.files?.[0]
            if (file) {
              onImport(file)
            }
          }}
        >
          <span className="dropzone-icon">📄</span>
          <span>Dépose ton CSV ici ou clique pour choisir un fichier</span>
        </div>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept={acceptedFileTypes || '.csv'}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) {
              onImport(file)
            }
            event.target.value = ''
          }}
        />
      </div>
    )
  }

  return null
}
