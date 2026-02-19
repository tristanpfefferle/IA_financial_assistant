import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../lib/supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(async () => ({
        data: {
          session: {
            access_token: 'token-123',
          },
        },
      })),
      refreshSession: vi.fn(async () => ({ data: { session: null }, error: null })),
    },
  },
}))

import { __unsafeResetSessionResetStateForTests, resetSession } from './agentApi'

describe('resetSession', () => {
  beforeEach(() => {
    __unsafeResetSessionResetStateForTests()
    vi.restoreAllMocks()
  })

  it('sends at most one request per lifecycle', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await resetSession()
    await resetSession()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent/reset-session'),
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
