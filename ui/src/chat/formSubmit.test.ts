import { describe, expect, it } from 'vitest'

import { formatFrenchDate } from './formatters'
import { buildFormSubmitPayload } from './formSubmit'

describe('formatFrenchDate', () => {
  it('formats an ISO date in french', () => {
    expect(formatFrenchDate('1995-05-10')).toBe('10 mai 1995')
  })

  it('returns input when ISO parsing is invalid', () => {
    expect(formatFrenchDate('10/05/1995')).toBe('10/05/1995')
  })
})

describe('buildFormSubmitPayload', () => {
  it('builds deterministic backend envelope for onboarding profile name', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_profile_name',
        title: 'Renseigne ton prénom et ton nom',
        submit_label: 'Valider',
        fields: [
          {
            id: 'first_name',
            label: 'Prénom',
            type: 'text',
            required: true,
          },
          {
            id: 'last_name',
            label: 'Nom',
            type: 'text',
            required: true,
          },
        ],
      },
      {
        first_name: 'Ada',
        last_name: 'Lovelace',
      },
    )

    expect(payload.humanText).toBe("Je m'appelle Ada Lovelace.")
    expect(payload.messageToBackend).toBe(
      'Prénom: Ada, Nom: Lovelace\n__ui_form_submit__:{"form_id":"onboarding_profile_name","values":{"first_name":"Ada","last_name":"Lovelace"}}',
    )
  })

  it('builds natural text for onboarding identity form while keeping backend payload unchanged', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_profile_identity',
        title: 'Renseigne ton prénom et ton nom',
        submit_label: 'Valider',
        fields: [
          {
            id: 'first_name',
            label: 'Prénom',
            type: 'text',
            required: true,
          },
          {
            id: 'last_name',
            label: 'Nom',
            type: 'text',
            required: true,
          },
        ],
      },
      {
        first_name: 'Ada',
        last_name: 'Lovelace',
      },
    )

    expect(payload.humanText).toBe("Je m'appelle Ada Lovelace.")
    expect(payload.messageToBackend).toBe(
      'Prénom: Ada, Nom: Lovelace\n__ui_form_submit__:{"form_id":"onboarding_profile_identity","values":{"first_name":"Ada","last_name":"Lovelace"}}',
    )
  })

  it('formats birth date in french for chat bubble text', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_birth_date',
        title: 'Date de naissance',
        submit_label: 'Valider',
        fields: [
          {
            id: 'birth_date',
            label: 'Date de naissance',
            type: 'date',
            required: true,
          },
        ],
      },
      {
        birth_date: '1995-05-10',
      },
    )

    expect(payload.humanText).toBe('Ma date de naissance est le 10 mai 1995.')
    expect(payload.messageToBackend).toBe(
      'Date de naissance: 1995-05-10\n__ui_form_submit__:{"form_id":"onboarding_birth_date","values":{"birth_date":"1995-05-10"}}',
    )
  })

  it('formats selected bank labels for onboarding bank accounts form', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_bank_accounts',
        title: 'Banques',
        submit_label: 'Valider',
        fields: [
          {
            id: 'selected_banks',
            label: 'Banques',
            type: 'multi_select',
            required: true,
            options: [
              { value: 'ubs', label: 'UBS' },
              { value: 'bcv', label: 'Banque Cantonale Vaudoise' },
            ],
          },
        ],
      },
      {
        selected_banks: ['ubs', 'bcv'],
      },
    )

    expect(payload.humanText).toBe('J’ai des comptes chez UBS et Banque Cantonale Vaudoise.')
    expect(payload.messageToBackend).toBe(
      'Banques: ubs, bcv\n__ui_form_submit__:{"form_id":"onboarding_bank_accounts","values":{"selected_banks":["ubs","bcv"]}}',
    )
  })

  it('falls back to value when a selected bank label is missing', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_bank_accounts',
        title: 'Banques',
        submit_label: 'Valider',
        fields: [
          {
            id: 'selected_banks',
            label: 'Banques',
            type: 'multi_select',
            required: true,
            options: [{ value: 'ubs', label: 'UBS' }],
          },
        ],
      },
      {
        selected_banks: ['ubs', 'unknown_bank'],
      },
    )

    expect(payload.humanText).toBe('J’ai des comptes chez UBS et unknown_bank.')
  })

  it('shows explicit fallback message when no bank is selected', () => {
    const payload = buildFormSubmitPayload(
      {
        type: 'ui_action',
        action: 'form',
        form_id: 'onboarding_bank_accounts',
        title: 'Banques',
        submit_label: 'Valider',
        fields: [
          {
            id: 'selected_banks',
            label: 'Banques',
            type: 'multi_select',
            required: true,
            options: [{ value: 'ubs', label: 'UBS' }],
          },
        ],
      },
      {
        selected_banks: [],
      },
    )

    expect(payload.humanText).toBe('Je n’ai pas encore choisi mes banques.')
  })
})
