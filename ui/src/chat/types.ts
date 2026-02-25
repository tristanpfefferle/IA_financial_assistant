export type ConsoleOption = {
  id: string
  label: string
  value: string
  tone?: 'positive' | 'negative' | 'neutral'
  disabled?: boolean
}

export type ConsoleUiState =
  | { mode: 'none' }
  | { mode: 'yes_no'; prompt?: string; yes: ConsoleOption; no: ConsoleOption }
  | { mode: 'single_primary'; prompt?: string; option: ConsoleOption }
  | { mode: 'options_grid'; prompt?: string; options: ConsoleOption[] }
  | { mode: 'options_list'; prompt?: string; options: ConsoleOption[] }
