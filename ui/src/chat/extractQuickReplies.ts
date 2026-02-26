import { toQuickReplyYesNoUiAction } from '../pages/chatUiRequests'

function normalizeReplyValue(option: { label: string; value: string }): string {
  const normalizedValue = option.value.trim().toLowerCase()
  if (normalizedValue.length > 0) {
    return normalizedValue
  }
  return option.label.trim().toLowerCase()
}

export function extractQuickReplies(toolResult: Record<string, unknown> | null | undefined): { id: string; label: string; value: string }[] | undefined {
  const action = toQuickReplyYesNoUiAction(toolResult)
  if (!action || action.options.length !== 2) {
    return undefined
  }

  const normalized = action.options.map(normalizeReplyValue)
  const hasYesNo = normalized.includes('oui') && normalized.includes('non')
  if (!hasYesNo) {
    return undefined
  }

  return action.options
}
