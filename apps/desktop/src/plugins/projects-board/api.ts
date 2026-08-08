/**
 * Projects data layer. Everything goes through `ctx.rest` — the plugin's
 * own `/api/plugins/projects-board/*` router
 * (`plugins/projects-board/dashboard/plugin_api.py`), which relays to
 * sandboxed.sh through the gateway's credential-free seam. No backend
 * credential ever reaches the renderer.
 *
 * Fetching/caching/polling is React Query's job (via the SDK). This module owns
 * the query keys, the REST calls, the persisted board atoms, and the pure
 * selectors/reducers the board and its tests share. A 503 means "no
 * sandboxed.sh on this host" — the surface hides itself; a 502 means the
 * backend is unreachable.
 */

import { atom, type PluginRestOptions, type PluginStorage, queryClient } from '@hermes/plugin-sdk'

export type MissionStatus = string

export interface MissionChip {
  github_pr: null | string
  id: null | string
  status: MissionStatus
  title: null | string
  updated_at: null | string
}

export interface TrackHealth {
  desired_state?: null | string
  status?: null | string
  track: string
  updated_at?: null | string
}

export interface ProjectHealth {
  active: number
  failed: number
  missions: number
  overdue: number
  tracks?: TrackHealth[]
  tracks_needing_attention: number
}

export interface DeliveryUpdate {
  at: null | string
  blocker?: null | string
  body?: null | string
  headline: string
  mode: null | string
}

export type ProjectBucket = 'active' | 'archived' | 'attention' | 'paused'

export interface ProjectRow {
  attention_reasons: string[]
  bucket: string
  conversation?: { session_id: string; source: string } | null
  health?: ProjectHealth
  latest_update: DeliveryUpdate | null
  missions: MissionChip[]
  /** Not in today's overview payload — rendered when the backend adds it. */
  next_action?: null | string
  slug: string
  title?: null | string
  updates_count?: number
}

export interface ProjectsResponse {
  projects: ProjectRow[]
}

export interface ProjectRecord {
  blocker?: null | string
  controller_cron_id?: null | string
  mode?: null | string
  next_action?: null | string
  objective?: null | string
  repository?: null | string
  slug: string
  status: string
  title?: null | string
  wait_ticks?: null | number
}

export interface ProjectGrant {
  budget_per_tick?: null | string
  material_bar?: null | string
  merge_authority?: null | string
  parallel_missions?: null | number
  pause_reason?: null | string
  resume_condition?: null | string
}

export interface ProjectDetail {
  conversation?: { session_id: string; source: string } | null
  grant?: null | ProjectGrant
  open_decisions?: Array<{ at: string; question: string; rationale?: null | string }>
  project: ProjectRecord
  tracks?: TrackHealth[]
}

export interface ProjectState {
  first_seen_at?: null | string
  headline: string
  last_seen_at?: null | string
  observations?: unknown
  signature: string
}

type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>
type Socket = (path: string, onMessage: (data: unknown) => void) => () => void

let rest: null | Rest = null

/** Whether the "how this board works" intro was dismissed. Persisted. */
export const $introDismissed = atom<boolean>(false)

/** Per-column collapse OVERRIDES (true=collapsed, false=expanded). Absence
 *  means auto: empty columns collapse to a rail. Persisted. */
export const $collapsedColumns = atom<Record<string, boolean>>({})

/** One-shot "open this project's drawer" request, so a command firing from
 *  OUTSIDE the board page (the sidebar row's menu) can reach it: the caller
 *  navigates to /board and parks the slug here; the page consumes and clears
 *  it on arrival. Ephemeral by design — never persisted. */
export const $openProjectSlug = atom<null | string>(null)

/** One-shot "focus the Needs-attention column" request (the statusbar pill's
 *  amber !N, an attention notification). The board consumes and clears it:
 *  expand + scroll to + briefly highlight the column. Ephemeral. */
export const $focusAttention = atom<boolean>(false)

/** Fire a desktop notification when a project ENTERS attention. Persisted. */
export const $notifyAttention = atom<boolean>(true)

/** Per-project epoch-ms of the last attention notification (30-min debounce).
 *  Persisted so an app restart doesn't re-fire a standing alert. */
export const $attentionNotifiedAt = atom<Record<string, number>>({})

const INTRO_KEY = 'introDismissed'
const COLLAPSED_KEY = 'collapsedColumns'
const NOTIFY_KEY = 'notifyAttention'
const NOTIFIED_AT_KEY = 'attentionNotifiedAt'

/** One `{invalidate, mission_id}` frame from the backend relay → refresh the
 *  roster. The poll stays as the fallback; the socket just makes a mission
 *  flipping to `awaiting_user` (or done) show up at once. */
function onEventFrame(data: unknown): void {
  const frame = data as { type?: string } | null

  if (frame?.type === 'invalidate') {
    void queryClient.invalidateQueries({ queryKey: PROJECTS_KEY })
  }
}

// A persisted, subscribable atom (the structural slice we need).
interface Persisted<T> {
  get(): T
  listen(cb: (value: T) => void): () => void
  set(value: T): void
}

/** Bind the plugin's doors at register time and return a disposer the host
 *  runs on unload/disable — so nothing (store sync, socket) survives a toggle
 *  or duplicates on re-enable. */
export function bindApi(restFn: Rest, storage?: PluginStorage, socket?: Socket): () => void {
  rest = restFn
  const unsubs: Array<() => void> = []

  if (storage) {
    const persist = <T>(store: Persisted<T>, key: string, fallback: T) => {
      store.set(storage.get(key, fallback))
      unsubs.push(store.listen(value => storage.set(key, value)))
    }

    persist($introDismissed, INTRO_KEY, false)
    persist($collapsedColumns, COLLAPSED_KEY, {})
    persist($notifyAttention, NOTIFY_KEY, true)
    persist($attentionNotifiedAt, NOTIFIED_AT_KEY, {})
  }

  previousBuckets = null

  const unsubscribe = socket ? socket('/events', onEventFrame) : null

  return () => {
    unsubs.forEach(unsub => unsub())
    unsubscribe?.()
    rest = null
  }
}

function call<T>(path: string, opts?: PluginRestOptions): Promise<T> {
  return rest ? rest<T>(path, opts) : Promise.reject(new Error('projects-board: API used before bindApi()'))
}

// ── query keys ───────────────────────────────────────────────────────────────

export const PROJECTS_KEY = ['projects-board', 'projects'] as const
export const projectKey = (slug: string) => ['projects-board', 'project', slug] as const
export const stateKey = (slug: string) => ['projects-board', 'state', slug] as const

// ── attention transitions (pure — unit-tested) ───────────────────────────────

/** Projects newly ENTERING attention relative to the previous roster snapshot.
 *  A null previous (startup / rebind) yields none — a standing alert the app
 *  boots into is not a transition. */
export function attentionTransitions(
  previous: null | Record<string, string>,
  projects: ProjectRow[]
): string[] {
  if (!previous) {
    return []
  }

  return projects
    .filter(p => p.bucket === 'attention' && p.slug in previous && previous[p.slug] !== 'attention')
    .map(p => p.slug)
}

/** 30-minute per-project debounce over the persisted notified-at record.
 *  Returns the slugs to notify NOW and stamps them. */
export function debounceAttentionNotifications(slugs: string[], now = Date.now()): string[] {
  const DEBOUNCE_MS = 30 * 60 * 1000
  const stamps = $attentionNotifiedAt.get()
  const due = slugs.filter(slug => now - (stamps[slug] ?? 0) >= DEBOUNCE_MS)

  if (due.length > 0) {
    $attentionNotifiedAt.set({ ...stamps, ...Object.fromEntries(due.map(slug => [slug, now])) })
  }

  return due
}

// The plugin shell owns presentation (i18n'd copy, the notification door);
// the data layer only detects transitions on each roster arrival.
let notifyAttentionFn: ((slug: string) => void) | null = null
let previousBuckets: null | Record<string, string> = null

export function setAttentionNotifier(fn: ((slug: string) => void) | null): void {
  notifyAttentionFn = fn
}

function observeRoster(projects: ProjectRow[]): void {
  const entered = attentionTransitions(previousBuckets, projects)
  previousBuckets = Object.fromEntries(projects.map(p => [p.slug, p.bucket]))

  if (entered.length === 0 || !$notifyAttention.get() || !notifyAttentionFn) {
    return
  }

  for (const slug of debounceAttentionNotifications(entered)) {
    notifyAttentionFn(slug)
  }
}

// ── palette rows (pure — unit-tested) ────────────────────────────────────────

export interface ProjectPaletteRow {
  kind: 'card' | 'chat'
  label: string
  sessionId?: string
  slug: string
}

/** ⌘K rows from the roster: an "open board card" row per non-archived project
 *  and an "open conversation" row when it has a binding. Capped (by project)
 *  so a huge roster can't flood the palette. Labeled by title, slug fallback. */
export function projectPaletteRows(projects: ProjectRow[], cap = 20): ProjectPaletteRow[] {
  return projects
    .filter(p => p.bucket !== 'archived')
    .slice(0, cap)
    .flatMap(p => {
      const rows: ProjectPaletteRow[] = []
      const sessionId = p.conversation?.session_id
      const label = p.title?.trim() || p.slug

      if (sessionId) {
        rows.push({ kind: 'chat', label, sessionId, slug: p.slug })
      }

      rows.push({ kind: 'card', label, slug: p.slug })

      return rows
    })
}

// ── reads ────────────────────────────────────────────────────────────────────

export const fetchProjects = () =>
  call<ProjectsResponse>('/projects').then(response => {
    observeRoster(response.projects)

    return response
  })

export const fetchProject = (slug: string) => call<ProjectDetail>(`/projects/${encodeURIComponent(slug)}`)

export const fetchProjectState = (slug: string, limit = 20) =>
  call<{ states: ProjectState[] }>(`/projects/${encodeURIComponent(slug)}/state?limit=${limit}`)

// ── writes ───────────────────────────────────────────────────────────────────

export type ProjectAction = 'archive' | 'pause' | 'resume' | 'unarchive'

export const projectAction = (slug: string, action: ProjectAction) =>
  call(`/projects/${encodeURIComponent(slug)}/action`, { body: { action }, method: 'POST' })

export const saveGrant = (slug: string, patch: Record<string, unknown>) =>
  call<{ grant: ProjectGrant }>(`/projects/${encodeURIComponent(slug)}/grant`, { body: patch, method: 'POST' })

export const steerMission = (missionId: string, content: string) =>
  call(`/missions/${encodeURIComponent(missionId)}/message`, { body: { content }, method: 'POST' })

// ── selectors ────────────────────────────────────────────────────────────────

/** Live missions in a project — the "agents" of the surface. */
export function liveMissions(project: ProjectRow): MissionChip[] {
  return project.missions.filter(m => m.status === 'active' || m.status === 'awaiting_user')
}

/** Missions that need the operator — the attention accents. */
export function needsAttention(project: ProjectRow): MissionChip[] {
  return project.missions.filter(m => m.status === 'awaiting_user')
}

/** The controller mode from the latest delivery, split into base + cause. */
export function projectMode(project: ProjectRow): { base: string; cause: null | string } | null {
  const raw = project.latest_update?.mode

  if (!raw) {
    return null
  }

  const [base, ...rest] = raw.trim().toLowerCase().split(':')

  if (base !== 'active' && base !== 'blocked' && base !== 'paused') {
    return null
  }

  const cause = rest.join(':').trim()

  return { base, cause: cause.length > 0 ? cause : null }
}

// ── card chip selection (pure — unit-tested) ─────────────────────────────────

/** Statuses that count as "live" for the card's chip row, in display order. */
const LIVE_CHIP_ORDER: Record<string, number> = { active: 1, awaiting_user: 0, queued: 2 }

const chipRecency = (chip: MissionChip): number => {
  const ms = chip.updated_at ? Date.parse(chip.updated_at) : Number.NaN

  return Number.isNaN(ms) ? 0 : ms
}

/** The card's compact chip row: at most `cap` chips — live ones first
 *  (awaiting_user, then active, then queued), then the most-recent failed —
 *  and the rest folded into one "+N" count. A card with nothing live never
 *  renders a wall of dead chips: at most ONE most-recent chip + "+N". The
 *  drawer keeps the full list. */
export function selectChips(
  missions: MissionChip[],
  cap = 3
): { chips: MissionChip[]; overflow: number } {
  const live = missions
    .filter(m => m.status in LIVE_CHIP_ORDER)
    .sort((a, b) => LIVE_CHIP_ORDER[a.status] - LIVE_CHIP_ORDER[b.status] || chipRecency(b) - chipRecency(a))

  const byRecency = (pool: MissionChip[]) => [...pool].sort((a, b) => chipRecency(b) - chipRecency(a))

  let chips: MissionChip[]

  if (live.length === 0) {
    chips = byRecency(missions).slice(0, Math.min(1, cap))
  } else {
    chips = live.slice(0, cap)

    if (chips.length < cap) {
      const failed = byRecency(missions.filter(m => m.status === 'failed'))
      chips = [...chips, ...failed.slice(0, cap - chips.length)]
    }
  }

  return { chips, overflow: missions.length - chips.length }
}

// ── board mechanics (pure — unit-tested) ─────────────────────────────────────

/** The board's fixed column order. `attention` is computed from health, so it
 *  is never a drop target — see `bucketAction`. */
export const BOARD_BUCKETS = ['attention', 'active', 'paused', 'archived'] as const

/** The lifecycle action a source→target column drag means, or null when the
 *  drag is meaningless (same column) or forbidden (`attention` is computed,
 *  not assigned — you resolve it, you don't drag it away). */
export function bucketAction(from: string, to: string): null | ProjectAction {
  if (from === to || from === 'attention' || to === 'attention') {
    return null
  }

  if (to === 'archived') {
    return 'archive'
  }

  if (from === 'archived') {
    // Unarchive restores the backend's own status — only the Active landing
    // spot is honest, so archived→paused is refused rather than guessed at.
    return to === 'active' ? 'unarchive' : null
  }

  return to === 'paused' ? 'pause' : 'resume'
}

/** Optimistically re-bucket a project (reconciled by the follow-up refetch). */
export function moveProject(data: ProjectsResponse, slug: string, toBucket: string): ProjectsResponse {
  let touched = false

  const projects = data.projects.map(project => {
    if (project.slug !== slug || project.bucket === toBucket) {
      return project
    }

    touched = true

    return { ...project, bucket: toBucket }
  })

  return touched ? { ...data, projects } : data
}
