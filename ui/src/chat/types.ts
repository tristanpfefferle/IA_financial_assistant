export type QuickReplyOption = { id: string; label: string; value: string; disabled?: boolean }

export type ChatUiState =
  | { mode: 'none' }
  | { mode: 'quick_replies'; prompt?: string; options: QuickReplyOption[] }
  | { mode: 'text'; placeholder?: string; submitLabel?: string }
  | {
      mode: 'quick_replies_text'
      prompt?: string
      options: QuickReplyOption[]
      placeholder?: string
      submitLabel?: string
    }
