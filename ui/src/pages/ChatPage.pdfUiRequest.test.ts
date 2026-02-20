import { describe, expect, it } from 'vitest'

import {
  claimPdfUiRequestExecution,
  toLegacyImportUiRequest,
  toOpenImportPanelUiAction,
  toPdfUiRequest,
} from './chatUiRequests'

describe('chatUiRequests', () => {
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

  it('returns null for invalid pdf payloads', () => {
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

  it('parses ui_action open_import_panel payload', () => {
    expect(
      toOpenImportPanelUiAction({
        type: 'ui_action',
        action: 'open_import_panel',
        bank_account_id: 'acc-1',
        accepted_types: ['csv'],
      }),
    ).toEqual({
      type: 'ui_action',
      action: 'open_import_panel',
      bank_account_id: 'acc-1',
      bank_account_name: undefined,
      accepted_types: ['csv'],
    })
  })

  it('parses import_file ui_request without bank account id', () => {
    expect(
      toLegacyImportUiRequest({
        type: 'ui_request',
        name: 'import_file',
        bank_account_name: 'Compte principal',
      }),
    ).toEqual({
      type: 'ui_request',
      name: 'import_file',
      bank_account_id: undefined,
      bank_account_name: 'Compte principal',
      accepted_types: ['csv', 'pdf'],
    })
  })
})
