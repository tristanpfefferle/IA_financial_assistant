export type PdfUiRequest = {
  type: 'ui_request'
  name: 'open_pdf_report'
  url: string
}

export function toPdfUiRequest(value: unknown): PdfUiRequest | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_request' || record.name !== 'open_pdf_report') {
    return null
  }

  const url = record.url
  if (typeof url !== 'string' || !url.trim()) {
    return null
  }

  return {
    type: 'ui_request',
    name: 'open_pdf_report',
    url: url.trim(),
  }
}

export function claimPdfUiRequestExecution(
  executedMessageIds: Set<string>,
  messageId: string,
  toolResult: unknown,
): PdfUiRequest | null {
  if (executedMessageIds.has(messageId)) {
    return null
  }

  const request = toPdfUiRequest(toolResult)
  if (!request) {
    return null
  }

  executedMessageIds.add(messageId)
  return request
}
