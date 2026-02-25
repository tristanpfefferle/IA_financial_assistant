import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { hardResetProfile, sendChatMessage } from '../api/agentApi'
import { ConsolePanel } from '../chat/ConsolePanel'
import { buildFormSubmitPayload } from '../chat/formSubmit'
import type { ConsoleOption, ConsoleUiState } from '../chat/types'
import { supabase } from '../lib/supabaseClient'
import type { FormUiAction } from './chatUiRequests'
import { toFormUiAction, toQuickReplyYesNoUiAction } from './chatUiRequests'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  toolResult?: Record<string, unknown> | null
  fromQuickReply?: boolean
}

type ChatMinimalPageProps = {
  email?: string
}

function createMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

function normalizeReplyValue(option: { label: string; value: string }): string {
  const normalizedValue = option.value.trim().toLowerCase()
  if (normalizedValue.length > 0) {
    return normalizedValue
  }
  return option.label.trim().toLowerCase()
}

function normalizeQuickReplyDisplay(label?: string, value?: string): string {
  const candidate = (label ?? value ?? '').trim()
  if (!candidate) {
    return ''
  }

  if (candidate === '✅') {
    return 'Oui.'
  }

  if (candidate === '❌') {
    return 'Non.'
  }

  const capitalized = candidate.charAt(0).toLocaleUpperCase('fr-CH') + candidate.slice(1)
  if (/[.!?…]$/.test(capitalized)) {
    return capitalized
  }

  return `${capitalized}.`
}

function mapQuickReplyOption(option: { id: string; label: string; value: string }, tone?: ConsoleOption['tone']): ConsoleOption {
  return {
    ...option,
    tone: tone ?? 'neutral',
  }
}

function extractPrompt(toolResult: Record<string, unknown> | null | undefined): string | undefined {
  if (!toolResult) {
    return undefined
  }
  return typeof toolResult.prompt === 'string' && toolResult.prompt.trim().length > 0 ? toolResult.prompt : undefined
}

function extractConsoleState(messages: ChatMessage[]): ConsoleUiState {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') {
      continue
    }

    const action = toQuickReplyYesNoUiAction(message.toolResult)
    if (action) {
      const prompt = extractPrompt(message.toolResult)
      if (action.options.length === 1) {
        return {
          mode: 'single_primary',
          prompt,
          option: mapQuickReplyOption(action.options[0], 'positive'),
        }
      }

      if (action.options.length === 2) {
        const [firstOption, secondOption] = action.options
        const normalizedFirst = normalizeReplyValue(firstOption)
        const normalizedSecond = normalizeReplyValue(secondOption)
        const hasYesNo = [normalizedFirst, normalizedSecond].includes('oui') && [normalizedFirst, normalizedSecond].includes('non')

        if (hasYesNo) {
          const yesSource = normalizedFirst === 'oui' ? firstOption : secondOption
          const noSource = normalizedFirst === 'non' ? firstOption : secondOption

          return {
            mode: 'yes_no',
            prompt,
            yes: mapQuickReplyOption(yesSource, 'positive'),
            no: mapQuickReplyOption(noSource, 'negative'),
          }
        }
      }

      if (action.options.length <= 12) {
        return {
          mode: 'options_grid',
          prompt,
          options: action.options.map((option) => mapQuickReplyOption(option)),
        }
      }

      return {
        mode: 'options_list',
        prompt,
        options: action.options.map((option) => mapQuickReplyOption(option)),
      }
    }

    if (toFormUiAction(message.toolResult)) {
      return { mode: 'none' }
    }

    break
  }

  return { mode: 'none' }
}

type ComposerMode = 'console' | 'form'

type FormCardProps = {
  formUiAction: FormUiAction
  isBusy: boolean
  onSubmitForm: (formId: FormUiAction['form_id'], values: Record<string, string | string[]>) => void
}

function FormCard({ formUiAction, isBusy, onSubmitForm }: FormCardProps) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initialValues: Record<string, string> = {}
    for (const field of formUiAction.fields) {
      initialValues[field.id] = field.value ?? field.default_value ?? ''
    }
    return initialValues
  })
  const [selectedMultiValues, setSelectedMultiValues] = useState<Record<string, Set<string>>>(() => {
    const initialSelected: Record<string, Set<string>> = {}
    for (const field of formUiAction.fields) {
      if (field.type !== 'multi_select' && field.type !== 'multi-select') {
        continue
      }
      const rawSelection = field.value ?? field.default_value ?? ''
      const selectedValues = rawSelection
        .split(',')
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
      initialSelected[field.id] = new Set(selectedValues)
    }
    return initialSelected
  })

  useEffect(() => {
    const nextValues: Record<string, string> = {}
    const nextSelected: Record<string, Set<string>> = {}
    for (const field of formUiAction.fields) {
      nextValues[field.id] = field.value ?? field.default_value ?? ''
      if (field.type === 'multi_select' || field.type === 'multi-select') {
        const rawSelection = field.value ?? field.default_value ?? ''
        nextSelected[field.id] = new Set(
          rawSelection
            .split(',')
            .map((item) => item.trim())
            .filter((item) => item.length > 0),
        )
      }
    }
    setValues(nextValues)
    setSelectedMultiValues(nextSelected)
  }, [formUiAction])

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const submitValues: Record<string, string | string[]> = { ...values }
    for (const field of formUiAction.fields) {
      if (field.type !== 'multi_select' && field.type !== 'multi-select') {
        continue
      }
      submitValues[field.id] = Array.from(selectedMultiValues[field.id] ?? new Set<string>())
    }

    onSubmitForm(formUiAction.form_id, submitValues)
  }

  useEffect(() => {
    for (const field of formUiAction.fields) {
      if ((field.type === 'multi_select' || field.type === 'multi-select') && (!field.options || field.options.length === 0)) {
        console.debug('[FormCard] multi_select field without options. Falling back to text input.', {
          formId: formUiAction.form_id,
          fieldId: field.id,
        })
      }
    }
  }, [formUiAction])

  const isRequiredMultiSelectMissing = formUiAction.fields.some((field) => {
    if (!field.required || (field.type !== 'multi_select' && field.type !== 'multi-select')) {
      return false
    }
    return (selectedMultiValues[field.id]?.size ?? 0) === 0
  })

  return (
    <form className="form-card" onSubmit={handleSubmit}>
      <p className="form-title">{formUiAction.title}</p>
      <div className="form-fields">
        {formUiAction.fields.map((field) => (
          <div key={field.id} className="form-field">
            <span>{field.label}</span>
            {field.type === 'multi_select' || field.type === 'multi-select' ? (
              field.options && field.options.length > 0 ? (
                <div className="form-multi-select-grid" role="group" aria-label={field.label}>
                  {field.options.map((option) => {
                    const selected = selectedMultiValues[field.id]?.has(option.value) ?? false
                    return (
                      <label key={option.id ?? `${field.id}-${option.value}`} className="form-multi-select-option">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={(event) => {
                            setSelectedMultiValues((current) => {
                              const nextSet = new Set(current[field.id] ?? [])
                              if (event.target.checked) {
                                nextSet.add(option.value)
                              } else {
                                nextSet.delete(option.value)
                              }
                              return {
                                ...current,
                                [field.id]: nextSet,
                              }
                            })
                          }}
                        />
                        <span>{option.label}</span>
                      </label>
                    )
                  })}
                </div>
              ) : (
                <input
                  id={`form-field-${field.id}`}
                  type="text"
                  value={values[field.id] ?? ''}
                  required={field.required}
                  placeholder={field.placeholder}
                  onChange={(event) => {
                    const nextValue = event.target.value
                    setValues((current) => ({
                      ...current,
                      [field.id]: nextValue,
                    }))
                  }}
                />
              )
            ) : (
              <input
                id={`form-field-${field.id}`}
                type={field.type === 'date' ? 'date' : field.type}
                value={values[field.id] ?? ''}
                required={field.required}
                placeholder={field.placeholder}
                onChange={(event) => {
                  const nextValue = event.target.value
                  setValues((current) => ({
                    ...current,
                    [field.id]: nextValue,
                  }))
                }}
              />
            )}
          </div>
        ))}
      </div>
      <button type="submit" disabled={isBusy || isRequiredMultiSelectMissing}>
        {formUiAction.submit_label || 'Continuer'}
      </button>
    </form>
  )
}

export function ChatMinimalPage({ email }: ChatMinimalPageProps) {
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isAssistantTyping, setIsAssistantTyping] = useState(false)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [canScroll, setCanScroll] = useState(false)
  const [debugUnlocked, setDebugUnlocked] = useState(() => localStorage.getItem('ui_debug_unlocked') === 'true')
  const [debugMode, setDebugMode] = useState(() => localStorage.getItem('ui_debug_mode') === 'true')
  const [headerMessage, setHeaderMessage] = useState<string | null>(null)
  const [submitErrorMessage, setSubmitErrorMessage] = useState<string | null>(null)

  const consoleState = useMemo(() => extractConsoleState(messages), [messages])
  const latestAssistant = useMemo(() => [...messages].reverse().find((message) => message.role === 'assistant') ?? null, [messages])
  const formUiAction = useMemo(() => toFormUiAction(latestAssistant?.toolResult), [latestAssistant])
  const composerMode: ComposerMode = formUiAction ? 'form' : 'console'

  function handleScroll() {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, scrollTop, clientHeight } = scrollRef.current
    const distanceBottom = scrollHeight - (scrollTop + clientHeight)
    setIsNearBottom(distanceBottom < 80)
    setCanScroll(scrollHeight > clientHeight + 8)
  }

  function scrollToBottom() {
    if (!scrollRef.current) {
      return
    }

    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }

  useEffect(() => {
    if (isNearBottom) {
      scrollToBottom()
    }
  }, [messages.length, isAssistantTyping, isNearBottom])

  useEffect(() => {
    if (!scrollRef.current) {
      return
    }

    const { scrollHeight, clientHeight } = scrollRef.current
    setCanScroll(scrollHeight > clientHeight + 8)
  }, [messages.length, isAssistantTyping])

  useEffect(() => {
    localStorage.setItem('ui_debug_mode', String(debugMode))
  }, [debugMode])

  useEffect(() => {
    localStorage.setItem('ui_debug_unlocked', String(debugUnlocked))
  }, [debugUnlocked])

  function handleUnlockDebug() {
    // TODO: move PIN to a secure/configurable source (backend or runtime config).
    const DEBUG_UNLOCK_PIN = '1234'
    const enteredPin = window.prompt('Entrer le code PIN debug')

    if (enteredPin === DEBUG_UNLOCK_PIN) {
      setHeaderMessage(null)
      setDebugUnlocked(true)
      return
    }

    setHeaderMessage('Code incorrect')
  }

  function handleLockDebug() {
    setDebugUnlocked(false)
    setDebugMode(false)
    localStorage.removeItem('ui_debug_unlocked')
    localStorage.removeItem('ui_debug_mode')
  }

  async function startConversation() {
    setHeaderMessage(null)
    setMessages([])
    setIsSending(false)
    setIsAssistantTyping(true)
    try {
      const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
      setMessages([
        {
          id: createMessageId(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
        },
      ])
    } catch {
      setMessages([
        {
          id: createMessageId(),
          role: 'assistant',
          content: 'Impossible de charger le message de bienvenue.',
          createdAt: Date.now(),
        },
      ])
    } finally {
      setIsAssistantTyping(false)
    }
  }

  useEffect(() => {
    let isMounted = true

    async function loadGreeting() {
      setIsAssistantTyping(true)
      try {
        const response = await sendChatMessage('', { debug: debugMode, requestGreeting: true })
        if (!isMounted) {
          return
        }

        setMessages([
          {
            id: createMessageId(),
            role: 'assistant',
            content: response.reply,
            createdAt: Date.now(),
            toolResult: response.tool_result,
          },
        ])
      } catch {
        if (!isMounted) {
          return
        }

        setMessages([
          {
            id: createMessageId(),
            role: 'assistant',
            content: 'Impossible de charger le message de bienvenue.',
            createdAt: Date.now(),
          },
        ])
      } finally {
        if (isMounted) {
          setIsAssistantTyping(false)
        }
      }
    }

    void loadGreeting()

    return () => {
      isMounted = false
    }
  }, [])

  async function submitMessage(text: string, displayContent?: string, fromQuickReply = false) {
    const trimmed = text.trim()
    if (!trimmed || isSending) {
      return
    }

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: displayContent?.trim().length ? displayContent : trimmed,
      createdAt: Date.now(),
      fromQuickReply,
    }

    setMessages((current) => [...current, userMessage])
    setSubmitErrorMessage(null)
    setIsSending(true)
    setIsAssistantTyping(true)

    try {
      const response = await sendChatMessage(trimmed, { debug: debugMode })
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: response.reply,
          createdAt: Date.now(),
          toolResult: response.tool_result,
        },
      ])
    } catch (error) {
      const content = error instanceof Error ? error.message : 'Erreur inconnue.'
      setSubmitErrorMessage(content)
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content,
          createdAt: Date.now(),
        },
      ])
    } finally {
      setIsSending(false)
      setIsAssistantTyping(false)
    }
  }

  function handleQuickReply(value: string, label?: string) {
    const displayContent = normalizeQuickReplyDisplay(label, value)
    void submitMessage(value, displayContent || value, true)
  }

  function handleFormSubmit(formId: FormUiAction['form_id'], values: Record<string, string | string[]>) {
    if (!formUiAction) {
      return
    }

    const payload = buildFormSubmitPayload(
      {
        ...formUiAction,
        form_id: formId,
      },
      values,
    )
    void submitMessage(payload.messageToBackend, payload.humanText)
  }

  function resetLocalChatState() {
    setMessages([])
    setIsSending(false)
    setIsAssistantTyping(false)
    setIsNearBottom(true)

    const chatStorageKeys = ['chat_messages', 'chat_debug_payload', 'chat_console_state']
    for (const key of chatStorageKeys) {
      window.localStorage.removeItem(key)
    }
  }

  async function handleHardReset() {
    if (!window.confirm('Confirmer le reset complet…')) {
      return
    }

    setHeaderMessage(null)
    try {
      await hardResetProfile()
      resetLocalChatState()
      await startConversation()
      setHeaderMessage('Reset terminé. Conversation redémarrée.')
    } catch (error) {
      setHeaderMessage(error instanceof Error ? error.message : 'Échec du reset complet.')
    }
  }


  async function handleLogout() {
    const { error } = await supabase.auth.signOut()
    if (error) {
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: 'assistant',
          content: `Erreur lors de la déconnexion: ${error.message}`,
          createdAt: Date.now(),
        },
      ])
      return
    }

    navigate('/login', { replace: true })
  }

  return (
    <main className="chat-layout" aria-label="Chat minimal">
      <div className="chat-frame">
        <div className="chat-stack">
          <header className="chat-min-header">
            <div className="chat-title-wrap">
              <strong>Assistant financier</strong>
              {debugUnlocked && debugMode ? <span className="debug-badge">Debug</span> : null}
            </div>
            <div className="chat-header-actions">
              <label className="debug-toggle" htmlFor="debug-mode-toggle">
                <input
                  id="debug-mode-toggle"
                  type="checkbox"
                  checked={debugMode}
                  onChange={(event) => setDebugMode(event.target.checked)}
                  disabled={!debugUnlocked}
                />
                Debug
              </label>
              {!debugUnlocked ? (
                <button type="button" className="secondary-button unlock-button" onClick={handleUnlockDebug}>
                  Déverrouiller
                </button>
              ) : (
                <button type="button" className="secondary-button" onClick={handleLockDebug}>
                  Verrouiller
                </button>
              )}
              {debugUnlocked && debugMode ? (
                <button type="button" className="secondary-button" onClick={() => { void handleHardReset() }}>
                  Reset
                </button>
              ) : null}
              {email ? <span className="subtle-text">{email}</span> : null}
              <button type="button" className="secondary-button" onClick={() => { void handleLogout() }}>
                Se déconnecter
              </button>
            </div>
          </header>
          {headerMessage ? <p className="subtle-text">{headerMessage}</p> : null}

          <div className="message-area">
            <div ref={scrollRef} className="chat-scroll" onScroll={handleScroll}>
              {messages.map((message, index) => {
                const isLastAssistantMessage =
                  message.role === 'assistant' && !messages.slice(index + 1).some((nextMessage) => nextMessage.role === 'assistant')
                const isShort = message.content.trim().length <= 18 && message.content.indexOf('\n') === -1
                const messageClasses = [
                  'msg',
                  message.role === 'user' ? 'msg-user' : 'msg-assistant',
                  ...(isShort ? ['msg-short'] : []),
                  ...(message.role === 'user' && message.fromQuickReply ? ['msg-chip'] : []),
                ].join(' ')

                return (
                  <div key={message.id} className={messageClasses}>
                    {message.content}
                    {debugUnlocked && debugMode && isLastAssistantMessage ? (
                      <details>
                        <summary>Debug payload</summary>
                        <pre>{JSON.stringify(message.toolResult ?? null, null, 2)}</pre>
                      </details>
                    ) : null}
                  </div>
                )
              })}
              {isAssistantTyping ? <div className="msg msg-assistant">...</div> : null}
            </div>
            {canScroll && !isNearBottom ? (
              <button type="button" className="scroll-down-btn" onClick={scrollToBottom} aria-label="Aller en bas">
                ↓
              </button>
            ) : null}
          </div>

          <div className="console-area">
            <div className="console-area-inner">
              {composerMode === 'form' ? (
                formUiAction ? <FormCard formUiAction={formUiAction} isBusy={isSending || isAssistantTyping} onSubmitForm={handleFormSubmit} /> : null
              ) : (
                <ConsolePanel uiState={consoleState} isSending={isSending} onChoose={handleQuickReply} />
              )}
            </div>
            {submitErrorMessage ? <p className="subtle-text">{submitErrorMessage}</p> : null}
          </div>
        </div>
      </div>
    </main>
  )
}
