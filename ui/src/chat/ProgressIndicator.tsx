import type { FormUiAction } from '../pages/chatUiRequests'

type StageId = 'profile' | 'banks' | 'import' | 'report'

type ProgressIndicatorProps = {
  messages: Array<{ role: 'user' | 'assistant'; content: string; toolResult?: Record<string, unknown> | null }>
  formUiAction: FormUiAction | null
}

type Stage = {
  id: StageId
  label: string
}

const STAGES: Stage[] = [
  { id: 'profile', label: 'Profil' },
  { id: 'banks', label: 'Banques' },
  { id: 'import', label: 'Import' },
  { id: 'report', label: 'Rapport' },
]

function detectStageFromForm(formUiAction: FormUiAction | null): StageId | null {
  if (!formUiAction) {
    return null
  }

  const haystack = [formUiAction.form_id, formUiAction.title, ...formUiAction.fields.map((field) => field.label)].join(' ').toLowerCase()
  if (/(profil|pr[ée]f[ée]rence|situation|personnel)/.test(haystack)) {
    return 'profile'
  }
  if (/(banque|compte|iban|bank)/.test(haystack)) {
    return 'banks'
  }
  if (/(import|csv|relev[ée]|fichier)/.test(haystack)) {
    return 'import'
  }
  if (/(rapport|bilan|r[ée]sum[ée]|insight|analyse)/.test(haystack)) {
    return 'report'
  }

  return null
}

function detectStageFromMessage(message: { content: string; toolResult?: Record<string, unknown> | null }): StageId | null {
  const toolText = message.toolResult ? JSON.stringify(message.toolResult).toLowerCase() : ''
  const text = `${message.content.toLowerCase()} ${toolText}`

  if (/(rapport|bilan|analyse|insight|statistique|r[ée]sum[ée])/.test(text)) {
    return 'report'
  }
  if (/(import|csv|relev[ée]|transaction|cat[ée]goris)/.test(text)) {
    return 'import'
  }
  if (/(banque|compte|iban|connect|agr[ée]gat)/.test(text)) {
    return 'banks'
  }
  if (/(profil|pr[ée]nom|nom|objectif|salaire|situation)/.test(text)) {
    return 'profile'
  }

  return null
}

function resolveActiveStage(
  messages: Array<{ role: 'user' | 'assistant'; content: string; toolResult?: Record<string, unknown> | null }>,
  formUiAction: FormUiAction | null,
): StageId {
  const fromForm = detectStageFromForm(formUiAction)
  if (fromForm) {
    return fromForm
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const stage = detectStageFromMessage(message)
    if (stage) {
      return stage
    }
  }

  return 'profile'
}

export function ProgressIndicator({ messages, formUiAction }: ProgressIndicatorProps) {
  const activeStage = resolveActiveStage(messages, formUiAction)
  const activeIndex = STAGES.findIndex((stage) => stage.id === activeStage)

  return (
    <div className="progress-indicator" aria-label="Progression de la configuration">
      <div className="progress-track" aria-hidden="true">
        <div className="progress-track-fill" style={{ width: `${(activeIndex / (STAGES.length - 1)) * 100}%` }} />
      </div>
      <ol className="progress-steps">
        {STAGES.map((stage, index) => {
          const stateClass = index < activeIndex ? 'is-complete' : index === activeIndex ? 'is-active' : 'is-upcoming'
          const symbol = index < activeIndex ? '✔' : index === activeIndex ? '●' : '○'

          return (
            <li key={stage.id} className={`progress-step ${stateClass}`}>
              <span className="progress-step-symbol" aria-hidden="true">
                {symbol}
              </span>
              <span>{stage.label}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
