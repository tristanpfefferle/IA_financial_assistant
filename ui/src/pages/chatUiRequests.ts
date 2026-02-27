export type ReportUiRequest = {
  type: 'ui_request'
  name: 'open_report'
  reportKind: 'spending'
  query: {
    month?: string
    start_date?: string
    end_date?: string
    bank_account_id?: string
  }
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


export type FormUiField = {
  id: string
  label: string
  type: 'text' | 'date' | 'multi_select' | 'multi-select'
  required: boolean
  placeholder?: string
  default_value?: string
  value?: string
  options?: Array<{ id?: string; label: string; value: string }>
}

export type FormUiAction = {
  type: 'ui_action'
  action: 'form'
  form_id:
    | 'onboarding_profile_name'
    | 'onboarding_profile_birth_date'
    | 'onboarding_profile_identity'
    | 'onboarding_birth_date'
    | 'onboarding_bank_accounts'
    | 'profile_update'
  title: string
  fields: FormUiField[]
  submit_label: string
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





export function toReportUiRequest(value: unknown): ReportUiRequest | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_request' || record.name !== 'open_report' || record.report_kind !== 'spending') {
    return null
  }

  const rawQuery = record.query
  const query: ReportUiRequest['query'] = {}
  if (rawQuery && typeof rawQuery === 'object') {
    const raw = rawQuery as Record<string, unknown>
    if (typeof raw.month === 'string' && raw.month.trim()) {
      query.month = raw.month.trim()
    }
    if (typeof raw.start_date === 'string' && raw.start_date.trim()) {
      query.start_date = raw.start_date.trim()
    }
    if (typeof raw.end_date === 'string' && raw.end_date.trim()) {
      query.end_date = raw.end_date.trim()
    }
    if (typeof raw.bank_account_id === 'string' && raw.bank_account_id.trim()) {
      query.bank_account_id = raw.bank_account_id.trim()
    }
  }

  return {
    type: 'ui_request',
    name: 'open_report',
    reportKind: 'spending',
    query,
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



export function toFormUiAction(value: unknown): FormUiAction | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  if (record.type !== 'ui_action' || record.action !== 'form') {
    return null
  }

  const formId = record.form_id
  const title = record.title
  const submitLabel = record.submit_label
  const fields = record.fields
  if (
    (
      formId !== 'onboarding_profile_name'
      && formId !== 'onboarding_profile_birth_date'
      && formId !== 'onboarding_profile_identity'
      && formId !== 'onboarding_birth_date'
      && formId !== 'onboarding_bank_accounts'
      && formId !== 'profile_update'
    )
    || typeof title !== 'string'
    || typeof submitLabel !== 'string'
    || !Array.isArray(fields)
  ) {
    return null
  }

  const parsedFields = fields
    .map((field): FormUiField | null => {
      if (!field || typeof field !== 'object') {
        return null
      }
      const raw = field as Record<string, unknown>
      if (
        typeof raw.id !== 'string'
        || typeof raw.label !== 'string'
        || (raw.type !== 'text' && raw.type !== 'date' && raw.type !== 'multi_select' && raw.type !== 'multi-select')
        || typeof raw.required !== 'boolean'
      ) {
        return null
      }
      const placeholder = typeof raw.placeholder === 'string' ? raw.placeholder : undefined
      const defaultValue = typeof raw.default_value === 'string' ? raw.default_value : undefined
      const value = typeof raw.value === 'string' ? raw.value : undefined
      const options = Array.isArray(raw.options)
        ? raw.options
            .map((option) => {
              if (!option || typeof option !== 'object') {
                return null
              }
              const parsed = option as Record<string, unknown>
              if (typeof parsed.label !== 'string' || typeof parsed.value !== 'string') {
                return null
              }
              return {
                ...(typeof parsed.id === 'string' ? { id: parsed.id } : {}),
                label: parsed.label,
                value: parsed.value,
              }
            })
            .filter((option): option is { id?: string; label: string; value: string } => option !== null)
        : undefined

      return {
        id: raw.id,
        label: raw.label,
        type: raw.type,
        required: raw.required,
        ...(placeholder ? { placeholder } : {}),
        ...(defaultValue !== undefined ? { default_value: defaultValue } : {}),
        ...(value !== undefined ? { value } : {}),
        ...(options && options.length > 0 ? { options } : {}),
      }
    })
    .filter((field): field is FormUiField => field !== null)

  if (parsedFields.length === 0) {
    return null
  }

  return {
    type: 'ui_action',
    action: 'form',
    form_id: formId,
    title,
    fields: parsedFields,
    submit_label: submitLabel,
  }
}
