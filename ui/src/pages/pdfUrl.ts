import { resolveApiBaseUrl } from '../api/agentApi'

export function resolvePdfReportUrl(url: string, apiBaseUrl?: string | null): string {
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url
  }

  const base = resolveApiBaseUrl(apiBaseUrl ?? undefined)
  if (url.startsWith('/')) {
    return `${base}${url}`
  }

  return `${base}/${url}`
}
