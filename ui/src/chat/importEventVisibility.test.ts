import { describe, expect, it } from 'vitest'

import { shouldRenderImportEvent } from './importEventVisibility'

describe('shouldRenderImportEvent', () => {
  it('hides debug events by default', () => {
    expect(shouldRenderImportEvent('debug', '')).toBe(false)
  })

  it('shows debug events when debug=1 is present', () => {
    expect(shouldRenderImportEvent('debug', '?debug=1')).toBe(true)
  })

  it('always shows non-debug events', () => {
    expect(shouldRenderImportEvent('import_progress', '')).toBe(true)
    expect(shouldRenderImportEvent('error', '')).toBe(true)
  })
})
