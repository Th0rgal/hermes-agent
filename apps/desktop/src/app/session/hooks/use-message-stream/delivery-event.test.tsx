import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'
import { playCompletionSound } from '@/lib/completion-sound'
import { $lastDeliveryPing, $unreadFinishedSessionIds, setSelectedStoredSessionId } from '@/store/session'
import type { RpcEvent } from '@/types/hermes'

import { useMessageStream } from './index'

vi.mock('@/lib/completion-sound', () => ({
  playCompletionSound: vi.fn()
}))

const SID = 'session-1'
let handleEvent: ((event: RpcEvent) => void) | null = null

function Harness() {
  const activeSessionIdRef = useRef<string | null>(SID)
  const sessionStateByRuntimeIdRef = useRef(new Map<string, ClientSessionState>())
  const queryClientRef = useRef(new QueryClient())

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

function emitDelivery(payload: RpcEvent['payload']) {
  act(() => handleEvent!({ payload, session_id: '', type: 'session.delivery' }))
}

beforeEach(() => {
  handleEvent = null
  $unreadFinishedSessionIds.set([])
  $lastDeliveryPing.set(null)
  setSelectedStoredSessionId(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useMessageStream session.delivery', () => {
  it('marks a background session unread, chimes, and pings the sync hook', async () => {
    await mountStream()

    emitDelivery({ delivery_label: 'watcher', delivery_message_id: 42, stored_session_id: 'bg-session' })

    expect($unreadFinishedSessionIds.get()).toEqual(['bg-session'])
    expect(playCompletionSound).toHaveBeenCalledWith('delivery:42')
    expect($lastDeliveryPing.get()?.sessionId).toBe('bg-session')
  })

  it('keeps the open session out of unread but still chimes and pings', async () => {
    setSelectedStoredSessionId('open-session')
    await mountStream()

    emitDelivery({ delivery_message_id: 7, stored_session_id: 'open-session' })

    expect($unreadFinishedSessionIds.get()).toEqual([])
    expect(playCompletionSound).toHaveBeenCalledWith('delivery:7')
    expect($lastDeliveryPing.get()?.sessionId).toBe('open-session')
  })

  it('ignores a delivery frame without a stored session id', async () => {
    await mountStream()

    emitDelivery({ delivery_label: 'watcher' })

    expect($unreadFinishedSessionIds.get()).toEqual([])
    expect(playCompletionSound).not.toHaveBeenCalled()
    expect($lastDeliveryPing.get()).toBeNull()
  })
})
