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

  return (
    <details>
      <summary>Debug payload</summary>
      <button type="button" className="secondary-button" onClick={() => void handleCopyJson()}>
        Copier JSON
      </button>
      {copyStatus === 'success' ? <p>JSON copié.</p> : null}
      {copyStatus === 'error' ? <p className="error-text">Copie impossible.</p> : null}

      <p>Warnings:</p>
      <pre>{stringifyPayload(warnings ?? null)}</pre>

      <p>merchant_alias_auto_resolve:</p>
      <pre>{stringifyPayload(merchantAliasInfo ?? null)}</pre>

      <p>tool_result:</p>
      <pre>{stringifyPayload(toolResult ?? null)}</pre>

      <p>plan:</p>
      <pre>{stringifyPayload(plan ?? null)}</pre>

      <p>Payload brut:</p>
      <pre>{payloadText}</pre>
    </details>
  )
}
