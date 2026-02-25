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
  values: Record<string, string>,
): { humanText: string; messageToBackend: string } {
  const humanText = formUiAction.fields.map((field) => `${field.label}: ${values[field.id] ?? ''}`).join(', ')
  const messageToBackend = `${humanText}\n${UI_FORM_SUBMIT_PREFIX}${JSON.stringify({
    form_id: formUiAction.form_id,
    values,
  })}`

  return {
    humanText,
    messageToBackend,
  }
}

