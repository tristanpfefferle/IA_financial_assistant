import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DebugPanel } from './DebugPanel'

describe('DebugPanel', () => {
  it('renders summary and payload keys', () => {
    const payload = {
      warnings: ['test warning'],
      tool_result: { ok: true },
    }

    const html = renderToStaticMarkup(<DebugPanel payload={payload} />)

    expect(html).toContain('Debug payload')
    expect(html).toContain('test warning')
    expect(html).toContain('tool_result')
  })


  it('shows resolve pending merchants button when import payload has remaining aliases', () => {
    const payload = {
      type: 'releves_import_result',
      merchant_alias_auto_resolve: {
        pending_total_count: 27,
      },
    }

    const html = renderToStaticMarkup(<DebugPanel payload={payload} />)

    expect(html).toContain('Résoudre les marchands restants')
  })

  it('handles unserializable payloads without crashing', () => {
    const payload: Record<string, unknown> = {}
    payload.self = payload

    const html = renderToStaticMarkup(<DebugPanel payload={payload} />)

    expect(html).toContain('[Unserializable payload]')
  })
})
