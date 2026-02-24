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

export type ImportClarificationResult = {
  ok: false
  type: 'clarification'
  message: string
  clarification_type?: string
}

export function isImportClarificationResult(
  value: unknown,
): value is ImportClarificationResult {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return v.ok === false && v.type === 'clarification' && typeof v.message === 'string'
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


export type PendingCategorizationItem = {
  id: string
  date: string
  montant: string | number
  devise: string
  libelle?: string | null
  payee?: string | null
  categorie?: string | null
  meta?: {
    category_key?: string | null
    category_status?: string | null
  } | null
}

export type PendingTransactionsResult = {
  count_total: number
  count_twint_p2p_pending: number
  items: PendingCategorizationItem[]
}

export type ResolvePendingMerchantAliasesResult = {
  ok: boolean
  type: string
  pending_before: number | null
  pending_after: number | null
  batches: number
  stats: Record<string, unknown>
}

export type SpendingReportApi = {
  period: {
    start_date: string
    end_date: string
    label: string
  }
  currency: string
  total: string
  count: number
  cashflow: {
    total_income: string
    total_expense: string
    net_cashflow: string
    internal_transfers: string
    net_including_transfers: string
    transaction_count: number
    currency: string
  }
  effective_spending: {
    outgoing: string
    incoming: string
    net_balance: string
    effective_total: string
  }
  categories: Array<{
    name: string
    amount: string
  }>
}

export type SpendingReport = {
  period: {
    start_date: string
    end_date: string
    label: string
  }
  currency: string
  total: number
  count: number
  cashflow: {
    total_income: number
    total_expense: number
    net_cashflow: number
    internal_transfers: number
    net_including_transfers: number
    transaction_count: number
    currency: string
  }
  effective_spending: {
    outgoing: number
    incoming: number
    net_balance: number
    effective_total: number
  }
  categories: Array<{
    name: string
    amount: number
  }>
}

export type SpendingReportParams = {
  month?: string
  start_date?: string
  end_date?: string
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

export async function sendChatMessage(message: string, options?: { debug?: boolean; requestGreeting?: boolean }): Promise<AgentChatResponse> {
  const headers = await buildAuthHeaders()
  if (options?.debug) {
    headers['X-Debug'] = '1'
  }

  const response = await fetch(`${getBaseUrl()}/agent/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, request_greeting: options?.requestGreeting ?? false }),
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

export async function importReleves(payload: ImportRequestPayload): Promise<RelevesImportResult | ImportClarificationResult> {
  const response = await fetch(`${getBaseUrl()}/finance/releves/import`, {
    method: 'POST',
    headers: await buildAuthHeaders(),
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API import (${response.status}): ${detail}`)
  }

  const result = (await response.json()) as unknown
  if (isImportClarificationResult(result)) {
    return result
  }

  return result as RelevesImportResult
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




export async function fetchPendingTransactions(): Promise<PendingTransactionsResult> {
  const response = await fetch(`${getBaseUrl()}/finance/transactions/pending`, {
    method: 'GET',
    headers: await buildAuthHeaders(),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API transactions pending (${response.status}): ${detail}`)
  }

  return (await response.json()) as PendingTransactionsResult
}

export async function openPdfFromUrl(url: string): Promise<void> {
  const accessToken = await getAccessToken()
  const resolvedUrl = url.startsWith('http://') || url.startsWith('https://') ? url : `${getBaseUrl()}${url}`
  const response = await fetch(resolvedUrl, {
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
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000)
}

export async function openSpendingReportPdf(month?: string): Promise<void> {
  const query = month ? `?month=${encodeURIComponent(month)}` : ''
  await openPdfFromUrl(`/finance/reports/spending.pdf${query}`)
}

function toNumberOrZero(value: unknown): number {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : 0
  }

  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value)
    return Number.isFinite(parsed) ? parsed : 0
  }

  return 0
}

export function normalizeSpendingReport(api: SpendingReportApi): SpendingReport {
  return {
    period: {
      start_date: api.period.start_date,
      end_date: api.period.end_date,
      label: api.period.label,
    },
    currency: api.currency,
    total: toNumberOrZero(api.total),
    count: api.count,
    cashflow: {
      total_income: toNumberOrZero(api.cashflow.total_income),
      total_expense: toNumberOrZero(api.cashflow.total_expense),
      net_cashflow: toNumberOrZero(api.cashflow.net_cashflow),
      internal_transfers: toNumberOrZero(api.cashflow.internal_transfers),
      net_including_transfers: toNumberOrZero(api.cashflow.net_including_transfers),
      transaction_count: api.cashflow.transaction_count,
      currency: api.cashflow.currency,
    },
    effective_spending: {
      outgoing: toNumberOrZero(api.effective_spending.outgoing),
      incoming: toNumberOrZero(api.effective_spending.incoming),
      net_balance: toNumberOrZero(api.effective_spending.net_balance),
      effective_total: toNumberOrZero(api.effective_spending.effective_total),
    },
    categories: api.categories.map((category) => ({
      name: category.name,
      amount: toNumberOrZero(category.amount),
    })),
  }
}

export async function getSpendingReport(params: SpendingReportParams = {}): Promise<SpendingReport> {
  const searchParams = new URLSearchParams()
  if (params.month) {
    searchParams.set('month', params.month)
  }
  if (params.start_date) {
    searchParams.set('start_date', params.start_date)
  }
  if (params.end_date) {
    searchParams.set('end_date', params.end_date)
  }

  const query = searchParams.toString()
  const response = await fetch(`${getBaseUrl()}/finance/reports/spending${query ? `?${query}` : ''}`, {
    method: 'GET',
    headers: await buildAuthHeaders(),
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    throw new Error(`Erreur API rapport JSON (${response.status}): ${detail}`)
  }

  const payload = (await response.json()) as SpendingReportApi
  return normalizeSpendingReport(payload)
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
