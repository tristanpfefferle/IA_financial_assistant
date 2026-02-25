import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from './ChatPage'

const { sendChatMessage, resolveApiBaseUrl, resetSession } = vi.hoisted(() => ({
  sendChatMessage: vi.fn(),
  resolveApiBaseUrl: vi.fn(() => 'http://127.0.0.1:8000'),
  resetSession: vi.fn(),
}))

const { logoutWithSessionReset } = vi.hoisted(() => ({
  logoutWithSessionReset: vi.fn(),
}))

vi.mock('../api/agentApi', () => ({
  sendChatMessage,
  resolveApiBaseUrl,
  resetSession,
}))

vi.mock('../lib/sessionLifecycle', () => ({
  logoutWithSessionReset,
}))

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      signOut: vi.fn(),
    },
  },
}))

vi.mock('react-virtuoso', () => ({
  Virtuoso: ({
    data,
    itemContent,
    components,
  }: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => ReactNode
    components?: { Footer?: () => ReactNode }
  }) => (
    <div data-testid="virtuoso-mock">
      {data.map((item, index) => (
        <div key={index}>{itemContent(index, item)}</div>
      ))}
      {components?.Footer ? <components.Footer /> : null}
    </div>
  ),
}))

describe('ChatPage', () => {
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    sendChatMessage.mockReset()
    resetSession.mockReset()
    logoutWithSessionReset.mockReset()
    resolveApiBaseUrl.mockReturnValue('http://127.0.0.1:8000')
  })

  afterEach(() => {
    document.body.removeChild(container)
  })

  it('renders chat shell and composer on initial load', async () => {
    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    expect(container.textContent).toContain('Assistant financier')
    expect(container.textContent).toContain('Connecté: user@example.com')
    expect(container.querySelector('textarea[aria-label="Message"]')).not.toBeNull()
  })

  it('sends a message and calls sendChatMessage', async () => {
    sendChatMessage.mockResolvedValue({ reply: 'Réponse assistant', tool_result: null, plan: null })

    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      valueSetter?.call(textarea, 'Bonjour')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })

    const form = container.querySelector('form') as HTMLFormElement
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(sendChatMessage).toHaveBeenCalledWith('Bonjour')
    expect(container.textContent).toContain('Bonjour')
  })

  it('renders assistant message when api response arrives', async () => {
    sendChatMessage.mockResolvedValue({ reply: 'Segment 1\n\nSegment 2', tool_result: null, plan: null })

    await act(async () => {
      createRoot(container).render(<ChatPage email="user@example.com" />)
    })

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      valueSetter?.call(textarea, 'Test')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })

    const form = container.querySelector('form') as HTMLFormElement
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })


    expect(container.textContent).toContain('Segment 1')
    expect(container.textContent).toContain('Segment 2')
  })
})
