import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'
import type { RpcEvent } from '@/types/hermes'

import { useMessageStream } from './index'

vi.mock('@/lib/completion-sound', () => ({
  playCompletionSound: vi.fn()
}))

const SID = 'session-1'
let handleEvent: ((event: RpcEvent) => void) | null = null
let sessionStates: Map<string, ClientSessionState> | null = null

function Harness() {
  const activeSessionIdRef = useRef<string | null>(SID)
  const sessionStateByRuntimeIdRef = useRef(new Map<string, ClientSessionState>())
  const queryClientRef = useRef(new QueryClient())

  sessionStates = sessionStateByRuntimeIdRef.current

  const stream = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession: vi.fn(async () => undefined),
    queryClient: queryClientRef.current,
    refreshHermesConfig: vi.fn(async () => undefined),
    refreshSessions: vi.fn(async () => undefined),
    sessionStateByRuntimeIdRef,
    updateSessionState: (sessionId, updater) => {
      const current = sessionStateByRuntimeIdRef.current.get(sessionId) ?? createClientSessionState()
      const next = updater(current)
      sessionStateByRuntimeIdRef.current.set(sessionId, next)

      return next
    }
  })

  useEffect(() => {
    handleEvent = stream.handleGatewayEvent
  }, [stream.handleGatewayEvent])

  return null
}

async function mountStream() {
  render(<Harness />)
  await waitFor(() => expect(handleEvent).not.toBeNull())
}

beforeEach(() => {
  handleEvent = null
  sessionStates = null
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('useMessageStream completion bursts', () => {
  it('mints unique bubble ids for completions landing in the same millisecond', async () => {
    await mountStream()

    // Freeze the clock: every id minted below sees the same Date.now(). Two
    // out-of-band completions processed in one tick (e.g. queued deliveries
    // flushed on reconnect) used to share `assistant-<ms>` — the duplicate id
    // then crashed assistant-ui's MessageRepository and the chat surface.
    vi.useFakeTimers()
    vi.setSystemTime(1700000000000)

    act(() => {
      handleEvent!({ payload: { text: 'canary callback failed' }, session_id: SID, type: 'message.complete' })
      handleEvent!({ payload: { text: 'lido synthesis ready' }, session_id: SID, type: 'message.complete' })
      handleEvent!({ payload: { text: 'canary callback failed again' }, session_id: SID, type: 'message.complete' })
    })

    const messages = sessionStates!.get(SID)!.messages
    const ids = messages.map(message => message.id)

    expect(messages).toHaveLength(3)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
