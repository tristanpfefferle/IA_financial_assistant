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
})
