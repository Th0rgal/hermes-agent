/**
 * PER-MESSAGE DELIVERY RECEIPTS — the "no send ever vanishes" ledger.
 *
 * Every user submission is tracked from the moment its optimistic bubble
 * renders until the gateway acks `prompt.submit`:
 *
 *   pending   → the submit is in flight (subtle spinner on the bubble)
 *   delivered → the gateway acked (brief check, then the entry auto-prunes)
 *   failed    → the submit threw, or NO ack arrived inside the timeout —
 *               a red retry affordance that PERSISTS until retried/dismissed
 *
 * The store is module-global and keyed by the CLIENT message id (the
 * optimistic bubble's id), with the owning session key alongside — so a
 * failed send survives view switches and session hops; only an explicit
 * retry/dismiss (or the containing bubble being rolled back) removes it.
 *
 * A late ack after a timeout-marked failure upgrades the receipt to
 * delivered: the server won the race after all, and leaving a retry button
 * up would invite a duplicate send.
 */

import { atom } from 'nanostores'

export type SendDeliveryState = 'delivered' | 'failed' | 'pending'

export interface SendReceipt {
  at: number
  id: string
  /** Stored session key the send belongs to (null before first-create). */
  sessionKey: null | string
  state: SendDeliveryState
  text: string
}

/** Client message id → receipt. */
export const $sendReceipts = atom<Record<string, SendReceipt>>({})

/** No gateway ack inside this window = the send is presumed lost. */
export const SEND_ACK_TIMEOUT_MS = 15_000

/** Delivered checks linger briefly, then the entry prunes itself. */
const DELIVERED_PRUNE_MS = 4_000

const timers = new Map<string, ReturnType<typeof setTimeout>>()

function clearTimer(id: string): void {
  const timer = timers.get(id)

  if (timer !== undefined) {
    clearTimeout(timer)
    timers.delete(id)
  }
}

function patch(id: string, changes: Partial<SendReceipt>): void {
  const receipts = $sendReceipts.get()
  const current = receipts[id]

  if (current) {
    $sendReceipts.set({ ...receipts, [id]: { ...current, ...changes } })
  }
}

/** Begin tracking a send the moment its bubble renders. */
export function trackSend(
  id: string,
  text: string,
  sessionKey: null | string,
  timeoutMs: number = SEND_ACK_TIMEOUT_MS
): void {
  clearTimer(id)
  $sendReceipts.set({
    ...$sendReceipts.get(),
    [id]: { at: Date.now(), id, sessionKey, state: 'pending', text }
  })
  timers.set(
    id,
    setTimeout(() => {
      timers.delete(id)

      // Silence past the deadline is a failure, never nothing.
      if ($sendReceipts.get()[id]?.state === 'pending') {
        patch(id, { state: 'failed' })
      }
    }, timeoutMs)
  )
}

/** The session key resolved after tracking began (create-on-send). */
export function rebindSendReceipt(id: string, sessionKey: string): void {
  patch(id, { sessionKey })
}

/** Gateway acked. Also upgrades a timeout-marked failure — the server won the
 *  race, and a lingering retry button would invite a duplicate send. */
export function markSendDelivered(id: string): void {
  clearTimer(id)

  if (!$sendReceipts.get()[id]) {
    return
  }

  patch(id, { state: 'delivered' })
  timers.set(
    id,
    setTimeout(() => {
      timers.delete(id)
      dropSendReceipt(id)
    }, DELIVERED_PRUNE_MS)
  )
}

/** The submit threw. The receipt persists until retried/dismissed. */
export function markSendFailed(id: string): void {
  clearTimer(id)
  patch(id, { state: 'failed' })
}

/** Forget a receipt (bubble rolled back, retry consumed it, user dismissed). */
export function dropSendReceipt(id: string): void {
  clearTimer(id)
  const receipts = $sendReceipts.get()

  if (id in receipts) {
    const { [id]: _dropped, ...rest } = receipts
    $sendReceipts.set(rest)
  }
}

/** Consume a failed receipt for retry: returns its text and removes it. */
export function takeFailedSend(id: string): null | string {
  const receipt = $sendReceipts.get()[id]

  if (!receipt || receipt.state !== 'failed') {
    return null
  }

  dropSendReceipt(id)

  return receipt.text
}
