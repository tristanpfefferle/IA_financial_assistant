import { describe, expect, it } from 'vitest'

import { claimPdfUiRequestExecution, toPdfUiRequest } from './chatUiRequests'

describe('toPdfUiRequest', () => {
  it('parses open_pdf_report ui request', () => {
    expect(
      toPdfUiRequest({
        type: 'ui_request',
        name: 'open_pdf_report',
        url: '/finance/reports/spending.pdf?month=2026-01',
      }),
    ).toEqual({
      type: 'ui_request',
      name: 'open_pdf_report',
      url: '/finance/reports/spending.pdf?month=2026-01',
    })
  })

  it('returns null for invalid payloads', () => {
    expect(toPdfUiRequest(null)).toBeNull()
    expect(toPdfUiRequest({ type: 'ui_request', name: 'import_file' })).toBeNull()
    expect(toPdfUiRequest({ type: 'ui_request', name: 'open_pdf_report', url: '' })).toBeNull()
  })

  it('does not re-execute same message id', () => {
    const executed = new Set<string>()
    const toolResult = {
      type: 'ui_request',
      name: 'open_pdf_report',
      url: '/finance/reports/spending.pdf?month=2026-01',
    }

    expect(claimPdfUiRequestExecution(executed, 'msg-1', toolResult)).not.toBeNull()
    expect(claimPdfUiRequestExecution(executed, 'msg-1', toolResult)).toBeNull()
  })
})
