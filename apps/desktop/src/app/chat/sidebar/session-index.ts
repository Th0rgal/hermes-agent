import { sessionRecency } from '@/app/chat/sidebar/projects/workspace-groups'
import { sessionPinId } from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

/**
 * Index sessions by every id a pin might be stored under.
 *
 * The sidebar fetches three independent slices — recents, cron, and messaging
 * — and renders the latter two in self-managed sections. Any of them can be
 * pinned, so all three must be indexed here or the Pinned section can't
 * resolve the pin to a row. A pinned session is also filtered out of its own
 * section, so failing to index it doesn't merely misplace the row: it removes
 * the session from the sidebar entirely.
 *
 * Each session is keyed under both its live id and its lineage root, so a pin
 * stored before an auto-compression still resolves to the live continuation
 * tip. When several loaded rows belong to the SAME conversation chain (the
 * stored root row and its compression continuation both appear in a slice),
 * every key resolves to the freshest row — the live tip — so a pinned row's
 * title, activity dot, and navigation follow the conversation instead of
 * freezing on the id the pin was stored under. Across DIFFERENT chains a
 * direct id collision keeps the previous semantics: recents (indexed last)
 * win.
 */
export function buildSessionByAnyId(
  visibleSessions: SessionInfo[],
  cronSessions: SessionInfo[],
  messagingSessions: SessionInfo[],
  fetchedSessions: SessionInfo[] = []
): Map<string, SessionInfo> {
  const map = new Map<string, SessionInfo>()

  const put = (key: string, session: SessionInfo, overwriteForeign: boolean) => {
    const current = map.get(key)

    if (!current) {
      map.set(key, session)

      return
    }

    if (sessionPinId(current) === sessionPinId(session)) {
      // Same conversation chain: the freshest row is the live tip. On an
      // exact recency tie, a direct-id hit from a later (higher-priority)
      // slice still wins — matching the foreign-collision rule below.
      const candidate = sessionRecency(session)
      const incumbent = sessionRecency(current)

      if (candidate > incumbent || (overwriteForeign && candidate === incumbent)) {
        map.set(key, session)
      }

      return
    }

    if (overwriteForeign) {
      map.set(key, session)
    }
  }

  // Individually fetched rows are indexed first (lowest priority for foreign
  // collisions) — they bridge chains none of the loaded slices carry.
  for (const session of [...fetchedSessions, ...cronSessions, ...messagingSessions, ...visibleSessions]) {
    put(session.id, session, true)

    if (session._lineage_root_id) {
      put(session._lineage_root_id, session, false)
    }
  }

  return map
}

/**
 * The row a pinned entry should DISPLAY. Resolution follows the chain to the
 * live tip (activity, dot, navigation), but a continuation tip carries an
 * auto-generated title while the user's rename landed on the row the pin was
 * stored under — the root. Prefer the root row's non-empty title so "Verity"
 * never becomes "Untitled session" after a rollover; everything else (id,
 * recency, status) stays the tip's. The stores don't distinguish explicit
 * from generated titles, so a non-empty root title is the pragmatic rule.
 */
export function pinnedDisplaySession(
  pinId: string,
  resolved: SessionInfo,
  exactRowById: Map<string, SessionInfo>
): SessionInfo {
  if (resolved.id === pinId) {
    return resolved
  }

  const rootTitle = exactRowById.get(pinId)?.title?.trim()

  if (!rootTitle || rootTitle === resolved.title) {
    return resolved
  }

  return { ...resolved, title: rootTitle }
}

/** Exact-id row index (no lineage aliasing) — the companion lookup
 *  `pinnedDisplaySession` needs to find the ROOT row the freshest-wins chain
 *  index no longer surfaces under its own id. */
export function buildExactSessionById(...slices: SessionInfo[][]): Map<string, SessionInfo> {
  const map = new Map<string, SessionInfo>()

  for (const slice of slices) {
    for (const session of slice) {
      if (!map.has(session.id)) {
        map.set(session.id, session)
      }
    }
  }

  return map
}
