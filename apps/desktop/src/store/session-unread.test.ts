import { beforeEach, describe, expect, it } from 'vitest'

import { $selectedStoredSessionId, $sessions, setSelectedStoredSessionId, setSessions } from '@/store/session'
import { $sessionLastSeenCounts, $sessionUnreadCounts, unreadCountFor } from '@/store/session-unread'
import type { SessionInfo } from '@/types/hermes'

const session = (id: string, messageCount: number, lineageRoot: null | string = null): SessionInfo => ({
  archived: false,
  cwd: null,
  ended_at: null,
  id,
  _lineage_root_id: lineageRoot,
  input_tokens: 0,
  is_active: false,
  last_active: 0,
  message_count: messageCount,
  model: null,
  output_tokens: 0,
  preview: null,
  source: null,
  started_at: 0,
  title: null,
  tool_call_count: 0
})

beforeEach(() => {
  setSessions([])
  $sessionLastSeenCounts.set({})
  setSelectedStoredSessionId(null)
})

describe('unreadCountFor', () => {
  it('counts messages past the last-seen baseline', () => {
    expect(unreadCountFor(session('a', 12), { a: 9 }, null)).toBe(3)
  })

  it('is 0 without a baseline — a fresh install marks nothing unread', () => {
    expect(unreadCountFor(session('a', 500), {}, null)).toBe(0)
  })

  it('is 0 for the open session and never negative', () => {
    expect(unreadCountFor(session('a', 12), { a: 9 }, 'a')).toBe(0)
    expect(unreadCountFor(session('a', 5), { a: 9 }, null)).toBe(0)
  })

  it('keys the baseline by the durable lineage id, so a rotated tip still resolves', () => {
    // The tip rotated a→a2; the baseline stamped under the root keeps working.
    expect(unreadCountFor(session('a2', 12, 'a'), { a: 9 }, null)).toBe(3)
    // Selecting by EITHER id of the chain counts as open.
    expect(unreadCountFor(session('a2', 12, 'a'), { a: 9 }, 'a')).toBe(0)
  })
})

describe('the stamp/clear loop', () => {
  it('opening a session stamps its baseline at the current count', () => {
    setSessions([session('a', 7)])
    setSelectedStoredSessionId('a')

    expect($sessionLastSeenCounts.get().a).toBe(7)
  })

  it('list refreshes re-stamp the OPEN session, so watched traffic never goes unread', () => {
    setSessions([session('a', 7)])
    setSelectedStoredSessionId('a')

    // Messages arrive while the user is looking at the session.
    setSessions([session('a', 11)])

    expect($sessionLastSeenCounts.get().a).toBe(11)

    // Navigating away leaves the badge at 0 until NEW messages arrive.
    setSelectedStoredSessionId(null)

    expect($sessionUnreadCounts.get().a).toBeUndefined()
  })

  it('counts new messages after close and clears on reopen', () => {
    setSessions([session('a', 7)])
    setSelectedStoredSessionId('a')
    setSelectedStoredSessionId(null)

    setSessions([session('a', 10)])

    expect($sessionUnreadCounts.get().a).toBe(3)

    // Reopen → the same signal that clears the core dot clears the count.
    setSelectedStoredSessionId('a')

    expect($sessionUnreadCounts.get().a).toBeUndefined()
    expect($sessionLastSeenCounts.get().a).toBe(10)
  })

  it('publishes the count under BOTH the live and the durable id', () => {
    setSessions([session('a', 7)])
    setSelectedStoredSessionId('a')
    setSelectedStoredSessionId(null)

    // Compression rotates the tip; the baseline rides the lineage root.
    setSessions([session('a2', 9, 'a')])

    const counts = $sessionUnreadCounts.get()

    expect(counts.a2).toBe(2)
    expect(counts.a).toBe(2)
  })
})

// Keep the imported store referenced so the module's stamp listeners bind.
void $selectedStoredSessionId
void $sessions
