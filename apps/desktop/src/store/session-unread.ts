/**
 * Per-session UNREAD MESSAGE COUNTS — the quantitative sibling of
 * `$unreadFinishedSessionIds` (a transient boolean set). The unit is the
 * session list's own `message_count` (state.db `sessions.message_count`, all
 * message rows — the payload doesn't split assistant from tool), which is
 * already flowing on every session-list refresh: no new polling here.
 *
 * The last-seen baseline is stamped from the SAME signal that clears the core
 * green dot — `$selectedStoredSessionId` (session.ts `setSelectedStoredSessionId`)
 * — so opening a session zeroes the badge exactly when the dot clears, and it
 * keeps re-stamping while the session stays open so live traffic the user is
 * watching never counts as unread. Baselines are keyed by the DURABLE lineage
 * id (`sessionPinId`) so they survive auto-compression's session-id rotation,
 * and persisted like pins/colors.
 *
 * A session with NO baseline counts 0 — a fresh install never paints every
 * historic conversation as unread.
 */

import { computed } from 'nanostores'

import { Codecs, persistentAtom } from '@/lib/persisted'
import {
  $cronSessions,
  $messagingSessions,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId,
  sessionPinId
} from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

/** Durable id → `message_count` at the moment the session was last open. */
export const $sessionLastSeenCounts = persistentAtom<Record<string, number>>(
  'hermes.desktop.sessionLastSeenCounts',
  {},
  Codecs.json<Record<string, number>>()
)

function allSessionSlices(): SessionInfo[] {
  return [...$sessions.get(), ...$cronSessions.get(), ...$messagingSessions.get()]
}

/** Stamp the baseline for the session currently identified by `storedSessionId`
 *  (any id along its compression chain). No-op when the session isn't in a
 *  loaded slice — there's no count to baseline against. */
export function stampSessionSeen(storedSessionId: null | string): void {
  if (!storedSessionId) {
    return
  }

  const session = allSessionSlices().find(candidate => sessionMatchesStoredId(candidate, storedSessionId))

  if (!session) {
    return
  }

  const key = sessionPinId(session)
  const prev = $sessionLastSeenCounts.get()

  if (prev[key] !== session.message_count) {
    $sessionLastSeenCounts.set({ ...prev, [key]: session.message_count })
  }
}

/** Pure count: messages since the baseline; 0 without a baseline, 0 while the
 *  session is the open one (the user is looking at it). */
export function unreadCountFor(
  session: SessionInfo,
  lastSeen: Record<string, number>,
  selectedStoredId: null | string
): number {
  if (selectedStoredId && sessionMatchesStoredId(session, selectedStoredId)) {
    return 0
  }

  const seen = lastSeen[sessionPinId(session)]

  return seen == null ? 0 : Math.max(0, session.message_count - seen)
}

// Opening a session stamps its baseline; list refreshes re-stamp the OPEN
// session so messages arriving while the user watches never become "unread"
// the moment they navigate away.
$selectedStoredSessionId.listen(id => stampSessionSeen(id))

for (const slice of [$sessions, $cronSessions, $messagingSessions]) {
  slice.listen(() => stampSessionSeen($selectedStoredSessionId.get()))
}

/** Session → unread count, nonzero entries only, keyed by BOTH the live id and
 *  the durable lineage id — so a consumer holding either side of a compression
 *  rotation (e.g. a backend binding that stored the original id) resolves the
 *  same count. */
export const $sessionUnreadCounts = computed(
  [$sessions, $cronSessions, $messagingSessions, $sessionLastSeenCounts, $selectedStoredSessionId],
  (sessions, cron, messaging, lastSeen, selectedId) => {
    const map: Record<string, number> = {}

    for (const session of [...sessions, ...cron, ...messaging]) {
      const count = unreadCountFor(session, lastSeen, selectedId)

      if (count > 0) {
        map[session.id] = count
        map[sessionPinId(session)] = count
      }
    }

    return map
  }
)
