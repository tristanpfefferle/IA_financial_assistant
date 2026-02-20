export type PdfUiRequest = {
  type: 'ui_request'
  name: 'open_pdf_report'
  url: string
}

export type OpenImportPanelUiAction = {
  type: 'ui_action'
  action: 'open_import_panel'
  bank_account_id?: string
  bank_account_name?: string
  accepted_types?: string[]
}

export type LegacyImportUiRequest = {
  type: 'ui_request'
  name: 'import_file'
  bank_account_id: string
  bank_account_name?: string
  accepted_types?: string[]
}

function normalizeAcceptedTypes(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return ['csv', 'pdf']
  }

  const normalized = value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim().replace(/^\./, '').toLowerCase())
    .filter((item) => item.length > 0)

  return normalized.length > 0 ? normalized : ['csv', 'pdf']
}

export function toOpenImportPanelUiAction(value: unknown): OpenImportPanelUiAction | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_action' || record.action !== 'open_import_panel') {
    return null
  }

  return {
    type: 'ui_action',
    action: 'open_import_panel',
    bank_account_id: typeof record.bank_account_id === 'string' ? record.bank_account_id : undefined,
    bank_account_name: typeof record.bank_account_name === 'string' ? record.bank_account_name : undefined,
    accepted_types: normalizeAcceptedTypes(record.accepted_types),
  }
}

export function toLegacyImportUiRequest(value: unknown): LegacyImportUiRequest | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_request' || record.name !== 'import_file') {
    return null
  }

  const bankAccountId = record.bank_account_id
  if (typeof bankAccountId !== 'string' || !bankAccountId.trim()) {
    return null
  }

  return {
    type: 'ui_request',
    name: 'import_file',
    bank_account_id: bankAccountId,
    bank_account_name: typeof record.bank_account_name === 'string' ? record.bank_account_name : undefined,
    accepted_types: normalizeAcceptedTypes(record.accepted_types),
  }
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
