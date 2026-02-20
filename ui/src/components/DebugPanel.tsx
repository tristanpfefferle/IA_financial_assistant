import { useEffect, useMemo, useRef, useState } from 'react'


type DebugPanelProps = {
  payload: unknown
}

function stringifyPayload(payload: unknown): string {
  try {
    return JSON.stringify(payload, null, 2)
  } catch (error) {
    return `[Unserializable payload] ${String(error)}`
  }
}

function pickMerchantAliasInfo(payload: unknown): Record<string, unknown> | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const record = payload as Record<string, unknown>
  const aliasInfo = record.merchant_alias_auto_resolve
  if (!aliasInfo || typeof aliasInfo !== 'object') {
    return null
  }

  const aliasRecord = aliasInfo as Record<string, unknown>
  return {
    attempted: aliasRecord.attempted,
    skipped_reason: aliasRecord.skipped_reason,
    stats: aliasRecord.stats,
  }
}

function getPendingAliasCount(payload: unknown): number {
  if (!payload || typeof payload !== 'object') {
    return 0
  }

  const payloadRecord = payload as Record<string, unknown>
  if (payloadRecord.type !== 'releves_import_result') {
    return 0
  }

  const aliasInfo = payloadRecord.merchant_alias_auto_resolve
  if (!aliasInfo || typeof aliasInfo !== 'object') {
    return 0
  }

  const pendingRaw = (aliasInfo as Record<string, unknown>).pending_total_count
  return typeof pendingRaw === 'number' && pendingRaw > 0 ? pendingRaw : 0
}

async function copyToClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'absolute'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

export function DebugPanel({ payload }: DebugPanelProps) {
  const [copyStatus, setCopyStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [resolvePendingResult, setResolvePendingResult] = useState<unknown>(null)
  const [resolvePendingError, setResolvePendingError] = useState<string | null>(null)
  const [isResolvePendingLoading, setIsResolvePendingLoading] = useState(false)
  const copyStatusTimeoutRef = useRef<number | null>(null)
  const payloadText = useMemo(() => stringifyPayload(payload), [payload])

  useEffect(() => {
    return () => {
      if (copyStatusTimeoutRef.current !== null) {
        window.clearTimeout(copyStatusTimeoutRef.current)
      }
    }
  }, [])

  const warnings = useMemo(() => {
    if (!payload || typeof payload !== 'object') {
      return undefined
    }
    return (payload as Record<string, unknown>).warnings
  }, [payload])

  const merchantAliasInfo = useMemo(() => pickMerchantAliasInfo(payload), [payload])
  const pendingAliasCount = useMemo(() => getPendingAliasCount(payload), [payload])

  const toolResult = useMemo(() => {
    if (!payload || typeof payload !== 'object') {
      return undefined
    }
    return (payload as Record<string, unknown>).tool_result
  }, [payload])

  const plan = useMemo(() => {
    if (!payload || typeof payload !== 'object') {
      return undefined
    }
    return (payload as Record<string, unknown>).plan
  }, [payload])

  async function handleCopyJson() {
    try {
      await copyToClipboard(payloadText)
      setCopyStatus('success')
    } catch {
      setCopyStatus('error')
    } finally {
      if (copyStatusTimeoutRef.current !== null) {
        window.clearTimeout(copyStatusTimeoutRef.current)
      }
      copyStatusTimeoutRef.current = window.setTimeout(() => {
        setCopyStatus('idle')
      }, 2000)
    }
  }

  async function handleResolvePendingAliases() {
    setIsResolvePendingLoading(true)
    setResolvePendingError(null)

    try {
      const api = await import('../api/agentApi')
      const result = await api.resolvePendingMerchantAliases({ limit: 20, max_batches: 10 })
      setResolvePendingResult(result)
    } catch (error) {
      setResolvePendingError(error instanceof Error ? error.message : 'Erreur inconnue')
    } finally {
      setIsResolvePendingLoading(false)
    }
  }

  return (
    <details>
      <summary>Debug payload</summary>
      <button type="button" className="secondary-button" onClick={() => void handleCopyJson()}>
        Copier JSON
      </button>
      {pendingAliasCount > 0 ? (
        <button
          type="button"
          className="secondary-button"
          onClick={() => void handleResolvePendingAliases()}
          disabled={isResolvePendingLoading}
        >
          {isResolvePendingLoading ? 'Résolution en cours…' : 'Résoudre les marchands restants'}
        </button>
      ) : null}
      {copyStatus === 'success' ? <p>JSON copié.</p> : null}
      {copyStatus === 'error' ? <p className="error-text">Copie impossible.</p> : null}

      <p>Warnings:</p>
      <pre>{stringifyPayload(warnings ?? null)}</pre>

      <p>merchant_alias_auto_resolve:</p>
      <pre>{stringifyPayload(merchantAliasInfo ?? null)}</pre>

      {resolvePendingError ? <p className="error-text">{resolvePendingError}</p> : null}
      <p>Resolve pending result:</p>
      <pre>{stringifyPayload(resolvePendingResult)}</pre>

      <p>tool_result:</p>
      <pre>{stringifyPayload(toolResult ?? null)}</pre>

      <p>plan:</p>
      <pre>{stringifyPayload(plan ?? null)}</pre>

      <p>Payload brut:</p>
      <pre>{payloadText}</pre>
    </details>
  )
}
