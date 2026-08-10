import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $sendReceipts,
  dropSendReceipt,
  markSendDelivered,
  markSendFailed,
  takeFailedSend,
  trackSend
} from '@/store/send-receipts'

beforeEach(() => {
  vi.useFakeTimers()
  $sendReceipts.set({})
})

afterEach(() => {
  for (const id of Object.keys($sendReceipts.get())) {
    dropSendReceipt(id)
  }

  vi.useRealTimers()
})

describe('send receipts', () => {
  it('tracks pending and marks delivered on the gateway ack', () => {
    trackSend('m1', 'hello', 'sess-a')
    expect($sendReceipts.get().m1.state).toBe('pending')

    markSendDelivered('m1')
    expect($sendReceipts.get().m1.state).toBe('delivered')

    // Delivered checks auto-prune; the ledger doesn't grow forever.
    vi.advanceTimersByTime(5_000)
    expect($sendReceipts.get().m1).toBeUndefined()
  })

  it('15s of silence marks the send failed — never nothing', () => {
    trackSend('m2', 'are you there?', 'sess-a')

    vi.advanceTimersByTime(14_999)
    expect($sendReceipts.get().m2.state).toBe('pending')

    vi.advanceTimersByTime(2)
    expect($sendReceipts.get().m2.state).toBe('failed')

    // Failure PERSISTS: no timer prunes it away.
    vi.advanceTimersByTime(600_000)
    expect($sendReceipts.get().m2.state).toBe('failed')
  })

  it('a late ack upgrades a timed-out failure (no duplicate-send bait)', () => {
    trackSend('m3', 'slow ack', 'sess-a')
    vi.advanceTimersByTime(20_000)
    expect($sendReceipts.get().m3.state).toBe('failed')

    markSendDelivered('m3')
    expect($sendReceipts.get().m3.state).toBe('delivered')
  })

  it('an explicit failure marks immediately and retry consumes the same text', () => {
    trackSend('m4', 'resend me', 'sess-b')
    markSendFailed('m4')
    expect($sendReceipts.get().m4.state).toBe('failed')

    // Retry hands back EXACTLY what was typed and clears the receipt.
    expect(takeFailedSend('m4')).toBe('resend me')
    expect($sendReceipts.get().m4).toBeUndefined()

    // Only failed receipts are consumable — a pending one must not retry.
    trackSend('m5', 'still in flight', 'sess-b')
    expect(takeFailedSend('m5')).toBeNull()
  })

  it('failure state survives anything short of retry/dismiss (view switches — global store)', () => {
    trackSend('m6', 'important', 'sess-c')
    markSendFailed('m6')

    // The store is module-global and session-keyed: nothing tied to a mounted
    // view holds it, so navigation can't lose it. Assert the entry carries its
    // session key for cross-view attribution.
    expect($sendReceipts.get().m6).toMatchObject({ sessionKey: 'sess-c', state: 'failed', text: 'important' })
  })
})
