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


export type QuickReplyYesNoUiAction = {
  type: 'ui_action'
  action: 'quick_replies'
  options: Array<{
    id: string
    label: string
    value: string
  }>
}

function parseQuickReplies(value: unknown): QuickReplyYesNoUiAction | null {
  if (!Array.isArray(value)) {
    return null
  }

  const options = value
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null
      }
      const record = item as Record<string, unknown>
      if (typeof record.id !== 'string' || typeof record.label !== 'string' || typeof record.value !== 'string') {
        return null
      }
      return {
        id: record.id,
        label: record.label,
        value: record.value,
      }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)

  if (options.length === 0) {
    return null
  }

  return {
    type: 'ui_action',
    action: 'quick_replies',
    options,
  }
}

export type LegacyImportUiRequest = {
  type: 'ui_request'
  name: 'import_file'
  bank_account_id?: string
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


export function toQuickReplyYesNoUiAction(value: unknown): QuickReplyYesNoUiAction | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type === 'ui_action' && record.action === 'quick_replies') {
    return parseQuickReplies(record.options)
  }

  if (Array.isArray(record.quick_replies)) {
    return parseQuickReplies(record.quick_replies)
  }

  // backward compatibility for previous contract
  if (record.type === 'ui_action' && record.action === 'quick_reply_yes_no') {
    return {
      type: 'ui_action',
      action: 'quick_replies',
      options: [
        { id: 'yes', label: '✅', value: 'oui' },
        { id: 'no', label: '❌', value: 'non' },
      ],
    }
  }

  return null
}

export function toLegacyImportUiRequest(value: unknown): LegacyImportUiRequest | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_request' || record.name !== 'import_file') {
    return null
  }

  const bankAccountId = typeof record.bank_account_id === 'string' ? record.bank_account_id.trim() : undefined

  return {
    type: 'ui_request',
    name: 'import_file',
    bank_account_id: bankAccountId || undefined,
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
