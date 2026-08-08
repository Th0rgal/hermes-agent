import { atom, computed } from 'nanostores'

import { sessionProjectColor } from '@/app/chat/sidebar/projects/workspace-groups'
import { getSession } from '@/hermes'
import { Codecs, persistentAtom } from '@/lib/persisted'
import { $projects } from '@/store/projects'
import { $cronSessions, $messagingSessions, $sessions, sessionMatchesStoredId, sessionPinId } from '@/store/session'
import type { ProjectInfo, SessionInfo } from '@/types/hermes'

// Per-session color OVERRIDES — a user-picked color that wins over the inherited
// project color (#66565 layer 2). Desktop-local like pins, keyed by the DURABLE
// lineage id so a color survives auto-compression's session-id rotation. To take
// this to the TUI later, promote this one atom to a backend SessionInfo.color
// field — the resolver below and the picker UI stay exactly as they are.
export const $sessionColorOverrides = persistentAtom<Record<string, string>>(
  'hermes.desktop.sessionColors',
  {},
  Codecs.stringRecord
)

// Set a session's override (null clears it → falls back to the project color).
export function setSessionColorOverride(durableId: string, color: null | string): void {
  const prev = $sessionColorOverrides.get()

  if (color) {
    $sessionColorOverrides.set({ ...prev, [durableId]: color })
  } else if (durableId in prev) {
    const next = { ...prev }
    delete next[durableId]
    $sessionColorOverrides.set(next)
  }
}

// The resolved color for every session, keyed by live session id — the ONE
// source of truth both the sidebar rows and the pane tabs read, so the two
// surfaces can never drift. Recomputed only when the session list, projects, or
// overrides change (all cold atoms; the working/streaming pulse lives in
// $sessionStates, so a busy flip never rebuilds this), and every consumer reads
// it as an O(1) lookup rather than re-deriving membership per render.
//
// Precedence in one place: an explicit per-session override wins over the
// inherited project color. Agent-set color (#66565 layer 3) slots in here too.
function resolveSessionColor(
  session: SessionInfo,
  projects: ProjectInfo[],
  overrides: Record<string, string>
): string | undefined {
  return overrides[sessionPinId(session)] ?? sessionProjectColor(session, projects) ?? undefined
}

// Sessions fetched INDIVIDUALLY to bridge ids the loaded lists don't carry —
// a LIVE conversation chain's tip (a backend binding hands us the newest
// continuation id) is often newer than anything in the paginated recents page,
// so its lineage root is unknowable from `$sessions` alone. `ensureSessionLineage`
// resolves such an id through `GET /api/sessions/:id` once and caches the row
// here; the color map below folds these in, so every subscriber repaints when
// a resolution lands.
export const $fetchedSessionsById = atom<Record<string, SessionInfo>>({})

const lineageFetchState = new Map<string, 'failed' | 'pending'>()

/** Resolve an id the loaded lists don't know (fire-and-forget, deduped).
 *  Success feeds `$fetchedSessionsById` → the color map recomputes. A failure
 *  is remembered so an unknown id never turns into a fetch loop. */
export function ensureSessionLineage(sessionId: string): void {
  if (lineageFetchState.has(sessionId) || sessionId in $fetchedSessionsById.get()) {
    return
  }

  lineageFetchState.set(sessionId, 'pending')

  // The gateway door can throw SYNCHRONOUSLY where no desktop bridge exists
  // (plain browser, jsdom) — this runs during render, so contain it.
  try {
    getSession(sessionId)
      .then(info => {
        lineageFetchState.delete(sessionId)
        $fetchedSessionsById.set({ ...$fetchedSessionsById.get(), [sessionId]: info })
      })
      .catch(() => {
        lineageFetchState.set(sessionId, 'failed')
      })
  } catch {
    lineageFetchState.set(sessionId, 'failed')
  }
}

/** Test seam: forget fetch outcomes (the cache atom is reset separately). */
export function resetLineageFetchState(): void {
  lineageFetchState.clear()
}

// EVERY slice a sidebar row can come from — recents, cron, messaging — the
// same union the Pinned section indexes (buildSessionByAnyId). The invariant
// is that an id whose Pinned row shows color X resolves to X from the id
// alone, and Pinned rows hold rows from all three slices: a controller/webhook
// conversation typically lives ONLY in the cron/messaging slice, and its
// INHERITED project color needs that row's cwd/git_repo_root — an override
// resolves without the row (direct key hit), which is why override'd sessions
// worked while inherited ones went grey.
export const $sessionColorById = computed(
  [$sessions, $cronSessions, $messagingSessions, $projects, $sessionColorOverrides, $fetchedSessionsById],
  (sessions, cron, messaging, projects, overrides, fetched) => {
    const map: Record<string, string> = {}

    // Fetched rows first, then the loaded lists — a session present in both
    // resolves through the fresher list row.
    for (const session of [...Object.values(fetched), ...sessions, ...cron, ...messaging]) {
      const color = resolveSessionColor(session, projects, overrides)

      if (color) {
        map[session.id] = color
      }
    }

    return map
  }
)

// The color for a single session object (the tabs already hold the SessionInfo
// they render, so they resolve through the same map the sidebar reads). A row
// that isn't in `$sessions` — e.g. a project-tree session older than the
// paginated recents page, opened as a tab — misses the map, so fall back to the
// same resolver the map is built from.
/** Resolve a color from a bare session ID — for surfaces that hold only an id
 *  (a plugin's backend binding, say), possibly EITHER side of a compression
 *  rotation. Precedence mirrors the object resolver: the live map, a direct
 *  override hit (the id may itself be the durable key), then the full resolver
 *  on whichever loaded session the id matches along its lineage chain. */
export function sessionColorForId(sessionId: null | string | undefined): string | undefined {
  if (!sessionId) {
    return undefined
  }

  const direct = $sessionColorById.get()[sessionId] ?? $sessionColorOverrides.get()[sessionId]

  if (direct) {
    return direct
  }

  const session = idToSession(sessionId)

  if (session) {
    return resolveSessionColor(session, $projects.get(), $sessionColorOverrides.get())
  }

  // A LIVE chain's tip that no loaded list carries: fetch its row once — the
  // arrival recomputes `$sessionColorById`, which subscribers already watch.
  ensureSessionLineage(sessionId)

  return undefined
}

/** The DURABLE id a color override for `sessionId` should be stored under —
 *  the matched session's lineage root when it's loaded, else the id itself
 *  (which is the root for a session outside the loaded page). */
export function sessionDurableId(sessionId: string): string {
  const session = idToSession(sessionId)

  return session ? sessionPinId(session) : sessionId
}

/** The loaded (or individually fetched) session a stored/tip id resolves to —
 *  searched across every slice the sidebar renders rows from, so id-only
 *  resolution sees exactly the rows the Pinned section holds. */
function idToSession(sessionId: string): SessionInfo | undefined {
  const loaded = [...$sessions.get(), ...$cronSessions.get(), ...$messagingSessions.get()]

  return (
    loaded.find(candidate => sessionMatchesStoredId(candidate, sessionId)) ??
    $fetchedSessionsById.get()[sessionId] ??
    Object.values($fetchedSessionsById.get()).find(candidate => sessionMatchesStoredId(candidate, sessionId))
  )
}

export function sessionColorFor(session: null | SessionInfo | undefined): string | undefined {
  if (!session) {
    return undefined
  }

  return (
    $sessionColorById.get()[session.id] ?? resolveSessionColor(session, $projects.get(), $sessionColorOverrides.get())
  )
}
