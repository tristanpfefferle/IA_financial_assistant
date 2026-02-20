import { supabase } from '../lib/supabaseClient'

export type AgentChatResponse = {
  reply: string
  tool_result: Record<string, unknown> | null
  plan: Record<string, unknown> | null
}

export type BankAccount = {
  id: string
  name: string
}

export type BankAccountsListResult = {
  items: BankAccount[]
}

export type RelevesImportError = {
  file: string
  row_index?: number | null
  message: string
}

export type RelevesImportPreviewItem = {
  date: string
  montant: string | number
  devise: string
  libelle?: string | null
  payee?: string | null
  categorie?: string | null
  bank_account_id?: string | null
}

export type RelevesImportResult = {
  imported_count: number
  failed_count: number
  duplicates_count: number
  replaced_count: number
  identical_count: number
  modified_count: number
  new_count: number
  requires_confirmation: boolean
  errors: RelevesImportError[]
  preview: RelevesImportPreviewItem[]
  ok?: boolean
  transactions_imported?: number
  transactions_imported_count?: number
  date_range?: { start: string; end: string } | null
  bank_account_id?: string | null
  bank_account_name?: string | null
}

export type ImportFilePayload = {
  filename: string
  content_base64: string
}

export type ImportRequestPayload = {
  files: ImportFilePayload[]
  bank_account_id?: string | null
  import_mode?: 'analyze' | 'commit'
  modified_action?: 'keep' | 'replace'
}

export type ResolvePendingMerchantAliasesPayload = {
  limit?: number
  max_batches?: number
}

export type PendingMerchantAliasesCountResult = {
  pending_total_count: number
}

export type ResolvePendingMerchantAliasesResult = {
  ok: boolean
  type: string
  pending_before: number | null
  pending_after: number | null
  batches: number
  stats: Record<string, unknown>
}

type ErrorPayload = {
  detail?: string | { message?: string }
}

function getBaseUrl(): string {
  const rawBaseUrl = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'
  return rawBaseUrl.replace(/\/+$/, '')
}

let sessionResetRequested = false

export function __unsafeResetSessionResetStateForTests(): void {
  sessionResetRequested = false
}

async function getAccessToken(): Promise<string | null> {
  const { data: sessionData } = await supabase.auth.getSession()
  if (sessionData.session?.access_token) {
    return sessionData.session.access_token
  }

  const { data: refreshedData, error } = await supabase.auth.refreshSession()
  if (error) {
    return null
  }

  return refreshedData.session?.access_token ?? null
}

async function extractErrorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as ErrorPayload
    if (typeof payload.detail === 'string' && payload.detail) {
      return payload.detail
    }
    if (payload.detail && typeof payload.detail === 'object' && payload.detail.message) {
      return payload.detail.message
    }
  } catch {
    // Empty body or non-JSON body.
  }

  return response.statusText || 'Erreur inconnue'
}

async function buildAuthHeaders(): Promise<Record<string, string>> {
  const accessToken = await getAccessToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  return headers
}

export async function sendChatMessage(message: string, options?: { debug?: boolean }): Promise<AgentChatResponse> {
  const headers = await buildAuthHeaders()
  if (options?.debug) {
    headers['X-Debug'] = '1'
  }

  const response = await fetch(`${getBaseUrl()}/agent/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message }),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API agent (${response.status}): ${detail}`)
  }

  return (await response.json()) as AgentChatResponse
}

export async function listBankAccounts(): Promise<BankAccountsListResult> {
  const response = await fetch(`${getBaseUrl()}/finance/bank-accounts`, {
    method: 'GET',
    headers: await buildAuthHeaders(),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API comptes (${response.status}): ${detail}`)
  }

  return (await response.json()) as BankAccountsListResult
}

export async function importReleves(payload: ImportRequestPayload): Promise<RelevesImportResult> {
  const response = await fetch(`${getBaseUrl()}/finance/releves/import`, {
    method: 'POST',
    headers: await buildAuthHeaders(),
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API import (${response.status}): ${detail}`)
  }

  return (await response.json()) as RelevesImportResult
}


export async function getPendingMerchantAliasesCount(): Promise<PendingMerchantAliasesCountResult> {
  const response = await fetch(`${getBaseUrl()}/finance/merchants/aliases/pending-count`, {
    method: 'GET',
    headers: await buildAuthHeaders(),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API pending marchands (${response.status}): ${detail}`)
  }

  return (await response.json()) as PendingMerchantAliasesCountResult
}


export async function resolvePendingMerchantAliases(
  payload: ResolvePendingMerchantAliasesPayload = {},
): Promise<ResolvePendingMerchantAliasesResult> {
  const response = await fetch(`${getBaseUrl()}/finance/merchants/aliases/resolve-pending`, {
    method: 'POST',
    headers: await buildAuthHeaders(),
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API résolution marchands (${response.status}): ${detail}`)
  }

  return (await response.json()) as ResolvePendingMerchantAliasesResult
}


export async function openSpendingReportPdf(month?: string): Promise<void> {
  const accessToken = await getAccessToken()
  const query = month ? `?month=${encodeURIComponent(month)}` : ''
  const response = await fetch(`${getBaseUrl()}/finance/reports/spending.pdf${query}`, {
    method: 'GET',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API rapport (${response.status}): ${detail}`)
  }

  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  window.open(blobUrl, '_blank', 'noopener,noreferrer')
}

export async function hardResetProfile(): Promise<void> {
  const response = await fetch(`${getBaseUrl()}/debug/hard-reset`, {
    method: 'POST',
    headers: await buildAuthHeaders(),
    body: JSON.stringify({ confirm: true }),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API reset (${response.status}): ${detail}`)
  }
}

export async function resetSession(options?: { keepalive?: boolean; timeoutMs?: number }): Promise<void> {
  if (sessionResetRequested) {
    return
  }

  sessionResetRequested = true

  const controller = new AbortController()
  const timeoutMs = options?.timeoutMs ?? 1500
  const timeoutId = window.setTimeout(() => {
    controller.abort()
  }, timeoutMs)

  try {
    const response = await fetch(`${getBaseUrl()}/agent/reset-session`, {
      method: 'POST',
      headers: await buildAuthHeaders(),
      signal: controller.signal,
      keepalive: options?.keepalive ?? false,
    })

    if (!response.ok) {
      return
    }
  } catch {
    // Best-effort request: errors are intentionally ignored.
  } finally {
    window.clearTimeout(timeoutId)
  }
}
