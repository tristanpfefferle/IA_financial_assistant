import type { FormUiAction } from '../pages/chatUiRequests'
import { formatFrenchDate } from './formatters'

const UI_FORM_SUBMIT_PREFIX = '__ui_form_submit__:'

/**
 * Backend form-submit protocol consumed by `agent/api.py::_parse_ui_form_submit_message`.
 *
 * Required message format:
 *  1) Human-readable text line (kept for chat transcript readability)
 *  2) New line + deterministic envelope `__ui_form_submit__:` followed by JSON object
 *
 * Example:
 * `Prénom: Ada, Nom: Lovelace\n__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Ada","last_name":"Lovelace"}}`
 */
export function buildFormSubmitPayload(
  formUiAction: FormUiAction,
  values: Record<string, string | string[]>,
): { humanText: string; messageToBackend: string } {
  const backendHumanText = formUiAction.fields
    .map((field) => `${field.label}: ${formatFieldValue(values[field.id])}`)
    .join(', ')
  const humanText = buildUiFormHumanText(formUiAction, values, backendHumanText)
  const messageToBackend = `${backendHumanText}\n${UI_FORM_SUBMIT_PREFIX}${JSON.stringify({
    form_id: formUiAction.form_id,
    values,
  })}`

  return {
    humanText,
    messageToBackend,
  }
}

function buildUiFormHumanText(
  formUiAction: FormUiAction,
  values: Record<string, string | string[]>,
  defaultText: string,
): string {
  if (formUiAction.form_id === 'onboarding_profile_identity' || formUiAction.form_id === 'onboarding_profile_name') {
    const firstName = values.first_name ?? ''
    const lastName = values.last_name ?? ''
    return `Je m'appelle ${firstName} ${lastName}.`
  }

  if (formUiAction.form_id === 'onboarding_profile_birth_date' || formUiAction.form_id === 'onboarding_birth_date') {
    const birthDate = typeof values.birth_date === 'string' ? formatFrenchDate(values.birth_date) : ''
    return `Ma date de naissance est le ${birthDate}.`
  }

  if (formUiAction.form_id === 'onboarding_bank_accounts') {
    return buildBankAccountsHumanText(formUiAction, values)
  }

  const yesNoText = normalizeYesNoValue(values)
  if (yesNoText) {
    return yesNoText
  }

  return defaultText
}

function normalizeYesNoValue(values: Record<string, string | string[]>): string | null {
  for (const value of Object.values(values)) {
    if (typeof value !== 'string') {
      continue
    }

    const normalized = value.trim().toLowerCase()
    if (normalized === 'oui') {
      return 'Oui.'
    }
    if (normalized === 'non') {
      return 'Non.'
    }
  }

  return null
}

function formatFieldValue(value: string | string[] | undefined): string {
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  return value ?? ''
}

function buildBankAccountsHumanText(
  formUiAction: FormUiAction,
  values: Record<string, string | string[]>,
): string {
  const multiSelectField = formUiAction.fields.find((field) => field.type === 'multi_select' || field.type === 'multi-select')
  if (!multiSelectField) {
    return 'Je n’ai pas encore choisi mes banques.'
  }

  const selectedValues = values[multiSelectField.id]
  if (!Array.isArray(selectedValues) || selectedValues.length === 0) {
    return 'Je n’ai pas encore choisi mes banques.'
  }

  const labels = selectedValues.map((selectedValue) => {
    const option = multiSelectField.options?.find((candidate) => candidate.value === selectedValue)
    return option?.label ?? selectedValue
  })

  return `J’ai des comptes chez ${formatFrenchList(labels)}.`
}

function formatFrenchList(items: string[]): string {
  if (items.length <= 1) {
    return items[0] ?? ''
  }

  if (items.length === 2) {
    return `${items[0]} et ${items[1]}`
  }

  return `${items.slice(0, -1).join(', ')} et ${items[items.length - 1]}`
}
