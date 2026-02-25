import { describe, expect, it } from 'vitest'

import { buildFormSubmitPayload } from './formSubmit'

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

    expect(payload.humanText).toBe('Prénom: Ada, Nom: Lovelace')
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
})
