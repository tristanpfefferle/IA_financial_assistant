import type { FormUiAction } from '../pages/chatUiRequests'

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
  if (formUiAction.form_id === 'onboarding_profile_identity') {
    const firstName = values.first_name ?? ''
    const lastName = values.last_name ?? ''
    return `Je m'appelle ${firstName} ${lastName}.`
  }

  if (formUiAction.form_id === 'onboarding_profile_birth_date' || formUiAction.form_id === 'onboarding_birth_date') {
    const birthDate = values.birth_date ?? ''
    return `Ma date de naissance est le ${birthDate}.`
  }

  if (formUiAction.form_id === 'onboarding_bank_accounts') {
    const bankAccounts = formatFieldValue(values.bank_accounts)
    return `J’ai des comptes chez: ${bankAccounts}.`
  }

  return defaultText
}

function formatFieldValue(value: string | string[] | undefined): string {
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  return value ?? ''
}
