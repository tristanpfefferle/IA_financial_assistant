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

  it('renders structured db_error details for tool errors', () => {
    const payload = {
      tool_result: {
        type: 'error',
        where: 'upsert_household_link',
        error_id: 'HHL-ABC123',
        db_error: {
          code: '23514',
          message: 'violates check constraint',
          details: 'Failing row contains null',
          hint: 'Provide a valid email',
        },
      },
    }

    const html = renderToStaticMarkup(<DebugPanel payload={payload} />)

    expect(html).toContain('db_error')
    expect(html).toContain('23514')
    expect(html).toContain('HHL-ABC123')
    expect(html).toContain('Provide a valid email')
  })
})
