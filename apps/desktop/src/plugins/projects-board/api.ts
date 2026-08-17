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

import {
  $goalsBySession,
  $projectBoundSessionIds,
  atom,
  type PluginRestOptions,
  type PluginStorage,
  queryClient
} from '@hermes/plugin-sdk'

export type MissionStatus = string

export interface MissionChip {
  github_pr: null | string
  id: null | string
  /** When the mission entered its current status. Heartbeats only bump updated_at. */
  last_status_change_at?: null | string
  status: MissionStatus
  title: null | string
  updated_at: null | string
}

export interface TrackHealth {
  active?: number
  desired_state?: null | string
  failed?: number
  last_activity_at?: null | string
  status?: null | string
  track: string
  updated_at?: null | string
  verdict?: string
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

export type AutonomyLevel = 'act_full' | 'act_reversible' | 'observe' | 'propose'

export interface ProjectRow {
  attention_reasons: string[]
  /** The grant's normalized autonomy level; absent on older backends. */
  autonomy_level?: AutonomyLevel | null
  bucket: string
  conversation?: { session_id: string; source: string } | null
  /** The roster record's controller↔project link, when declared. */
  controller_cron_id?: null | string
  /** Last successful scheduler run of the linked controller job — proves the
   *  controller is alive even when [SILENT] ticks deliver no update. */
  controller_heartbeat_at?: null | string
  /** Honesty read-model axes (server-authoritative, from sandboxed.sh). */
  controller_health?: 'healthy' | 'missing' | 'stale' | null
  delivery_health?: 'dropped' | 'misrouted' | 'reaching_user' | null
  health?: ProjectHealth
  latest_update: DeliveryUpdate | null
  missions: MissionChip[]
  /** The controller-reported mode (`active`/`blocked`/`paused`[:cause]). */
  mode?: null | string
  /** Not in today's overview payload — rendered when the backend adds it. */
  next_action?: null | string
  /** The operator's board override (`paused`/`archived`), when set. */
  override?: null | string
  /** Ledger decisions waiting on the owner; absent on older backends. */
  pending_decisions?: number
  progress_state?: 'blocked' | 'waiting_external' | 'working' | null
  slug: string
  /** The roster's display title (humanized slug fallback), served by
   *  sandboxed.sh; surfaces render it instead of the raw slug when present. */
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
  autonomy_level?: AutonomyLevel | null
  budget_per_tick?: null | string
  material_bar?: null | string
  merge_authority?: null | string
  parallel_missions?: null | number
  pause_reason?: null | string
  resume_condition?: null | string
}

/** One ledger row: an owner escalation or a declared autonomous act. The new
 *  fields are absent on older backends — render as a plain question then. */
export interface ProjectDecision {
  answer?: null | string
  answered_at?: null | string
  at: string
  /** granted (autonomous act) | escalation (question for the owner). */
  authority?: null | string
  evidence?: null | { mission_id?: null | string; pr_url?: null | string }
  kind?: null | string
  question: string
  rationale?: null | string
  /** decided | pending_user | answered | expired. */
  status?: null | string
}

export interface ProjectItemAttempt {
  id: string
  role?: null | string
  status: string
  title?: null | string
  updated_at: string
}

export interface ProjectItem {
  attempts: ProjectItemAttempt[]
  desired_state?: null | string
  key: string
  kind?: string
  open: boolean
  status?: null | string
}

export interface ProjectDetail {
  conversation?: { session_id: string; source: string } | null
  grant?: null | ProjectGrant
  items?: ProjectItem[]
  open_decisions?: ProjectDecision[]
  project: ProjectRecord
  /** Autonomous acts + answered escalations, newest first. */
  recent_decisions?: ProjectDecision[]
  tracks?: TrackHealth[]
}

/** One roadmap item — a board task aggregated up to the project. */
export interface ProjectTask {
  acceptance_criteria?: string[]
  attempts?: number
  boss_mission_id?: null | string
  depends_on?: string[]
  id?: null | string
  pr_url?: null | string
  result_digest?: null | string
  status: string
  task_key: string
  title: string
  updated_at?: null | string
  worker_mission_id?: null | string
}

export interface ProjectTasksResponse {
  summary?: { done: number; failed: number; running: number; total: number }
  tasks: ProjectTask[]
}

/** Only an explicit binding is a writable Desktop conversation.  The
 * `latest_update` fallback is usually a one-shot cron/webhook session and must
 * never be offered as a chat target. */
export function isBoundConversation(
  conversation?: { session_id: string; source: string } | null
): conversation is { session_id: string; source: 'binding' } {
  return conversation?.source === 'binding' && Boolean(conversation.session_id)
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

/** Chat project rail collapsed (chip instead of panel). Persisted. */
export const $railCollapsed = atom<boolean>(false)

const INTRO_KEY = 'introDismissed'
const RAIL_KEY = 'railCollapsed'
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
    persist($railCollapsed, RAIL_KEY, false)
  }

  previousBuckets = null

  const unsubscribe = socket ? socket('/events', onEventFrame) : null

  let lastGoalPost = ''

  const publishBoundGoals = () => {
    const goals = $goalsBySession.get()
    const bindings = $projectBoundSessionIds.get()

    for (const [sid, slug] of Object.entries(bindings)) {
      const title = sessionGoalLead(goals[sid])

      if (!title || !slug) {
        continue
      }

      const key = `${slug}\0${title}`

      if (key === lastGoalPost) {
        continue
      }

      lastGoalPost = key
      void publishProjectGoal(slug, title).catch(() => {
        if (lastGoalPost === key) {
          lastGoalPost = ''
        }
      })
    }
  }

  unsubs.push($goalsBySession.listen(publishBoundGoals))
  unsubs.push($projectBoundSessionIds.listen(publishBoundGoals))

  return () => {
    unsubs.forEach(unsub => unsub())
    unsubscribe?.()
    // Disabled plugin = no Projects section; release the sessions back to
    // the core Recents list instead of leaving them hidden everywhere.
    lastRoster = []
    resolvedLiveIds = {}
    $projectBoundSessionIds.set({})
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
export const tasksKey = (slug: string) => ['projects-board', 'tasks', slug] as const

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
let notifyAttentionFn: ((label: string) => void) | null = null
let previousBuckets: null | Record<string, string> = null

export function setAttentionNotifier(fn: ((label: string) => void) | null): void {
  notifyAttentionFn = fn
}

// ── binding canonicalization ────────────────────────────────────────────────
// Bindings store a session id frozen at bind time; Hermes compressions move
// the conversation to new ids. The gateway resolves stored → live, and both
// ids are published for the core dedup/selection seam. `lastRoster` +
// `resolvedLiveIds` are composed by ONE publisher so a roster refresh can't
// transiently drop the resolved ids.

let lastRoster: ProjectRow[] = []
let resolvedLiveIds: Record<string, string> = {}

function publishBindings(): void {
  const out: Record<string, string> = {}

  for (const project of lastRoster) {
    if (project.bucket === 'archived' || !isBoundConversation(project.conversation)) {
      continue
    }

    const stored = project.conversation.session_id
    out[stored] = project.slug
    const live = resolvedLiveIds[stored]

    if (live) {
      out[live] = project.slug
    }
  }

  $projectBoundSessionIds.set(out)
}

export const resolveLiveSessionId = (sessionId: string) =>
  call<{ live_session_id: string }>(`/sessions/${encodeURIComponent(sessionId)}/resolve`).then(
    response => response.live_session_id
  )

/** Record a stored→live resolution (from `useLiveBindings`) and republish. */
export function registerLiveResolution(storedId: string, liveId: string): void {
  if (resolvedLiveIds[storedId] === liveId) {
    return
  }

  resolvedLiveIds = { ...resolvedLiveIds, [storedId]: liveId }
  publishBindings()
}

/** The live id a binding currently resolves to (falls back to the stored id
 *  until — or unless — a resolution lands). */
export function liveSessionIdFor(storedId: string): string {
  return resolvedLiveIds[storedId] ?? storedId
}

/** Re-point a project's bound conversation (the self-heal write). */
export const rebindConversation = (slug: string, sessionId: string) =>
  call(`/projects/${encodeURIComponent(slug)}/conversation`, {
    body: { session_id: sessionId },
    method: 'PUT'
  })

function observeRoster(projects: ProjectRow[]): void {
  // Tell the core sidebar which sessions live under the Projects section, so
  // its Recents list dedupes them. Archived projects release their session
  // back to the flat list.
  lastRoster = projects
  publishBindings()

  const entered = attentionTransitions(previousBuckets, projects)
  previousBuckets = Object.fromEntries(projects.map(p => [p.slug, p.bucket]))

  if (entered.length === 0 || !$notifyAttention.get() || !notifyAttentionFn) {
    return
  }

  // Notify by the human display label — the backend always populates
  // ProjectRow.title (humanize_slug fallback), so a raw slug should never
  // surface. Debounce still keys on slugs; only the shown label changes.
  const labels = Object.fromEntries(projects.map(p => [p.slug, p.title?.trim() || p.slug]))

  for (const slug of debounceAttentionNotifications(entered)) {
    notifyAttentionFn(labels[slug] ?? slug)
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
 *  so a huge roster can't flood the palette. The overview payload carries the
 *  display title (humanize_slug fallback), so rows are labeled by it. */
export function projectPaletteRows(projects: ProjectRow[], cap = 20): ProjectPaletteRow[] {
  return projects
    .filter(p => p.bucket !== 'archived')
    .slice(0, cap)
    .flatMap(p => {
      const rows: ProjectPaletteRow[] = []
      const label = p.title?.trim() || p.slug
      const sessionId = isBoundConversation(p.conversation) ? p.conversation.session_id : undefined

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

export const fetchProjectTasks = (slug: string) =>
  call<ProjectTasksResponse>(`/projects/${encodeURIComponent(slug)}/tasks`)

// ── writes ───────────────────────────────────────────────────────────────────

export type ProjectAction = 'archive' | 'delete' | 'pause' | 'resume' | 'unarchive'

export const projectAction = (slug: string, action: ProjectAction) =>
  call(`/projects/${encodeURIComponent(slug)}/action`, { body: { action }, method: 'POST' })

/** Rename a project's display title. Relays to the sandboxed.sh upsert (which
 *  COALESCEs the title), leaving objective/repository/controller untouched. */
export const renameProject = (slug: string, title: string) =>
  call(`/projects/${encodeURIComponent(slug)}/rename`, { body: { title }, method: 'POST' })

export const saveGrant = (slug: string, patch: Record<string, unknown>) =>
  call<{ grant: ProjectGrant }>(`/projects/${encodeURIComponent(slug)}/grant`, { body: patch, method: 'POST' })

/** Answer a pending owner decision. The relay flips the ledger row AND queues
 *  the answer into the bound control conversation (best-effort `injected`). */
export const answerDecision = (slug: string, at: string, answer: string, question?: string) =>
  call<{ injected: boolean; ok: boolean }>(`/projects/${encodeURIComponent(slug)}/decisions/answer`, {
    body: { answer, at, question },
    method: 'POST'
  })

export const steerMission = (missionId: string, content: string) =>
  call(`/missions/${encodeURIComponent(missionId)}/message`, { body: { content }, method: 'POST' })

/** Persist a bound-session /goal as the project's next_action. */
export const publishProjectGoal = (slug: string, nextAction: string, mode?: string) =>
  call(`/projects/${encodeURIComponent(slug)}/status`, {
    body: { mode, next_action: nextAction },
    method: 'POST'
  })

// ── selectors ────────────────────────────────────────────────────────────────

/** Live missions in a project — the "agents" of the surface. */
export function liveMissions(project: ProjectRow): MissionChip[] {
  return project.missions.filter(
    m =>
      m.status === 'active' ||
      m.status === 'pending' ||
      m.status === 'waiting_background' ||
      m.status === 'awaiting_user'
  )
}

/** Controller next_action that is just "inspect <dead uuid>" — not a roadmap. */
export function isInspectNextAction(next: null | string | undefined): boolean {
  return /^\s*inspect\s+[0-9a-f-]{8,}/i.test(next ?? '')
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

// ── controller status (pure — unit-tested) ───────────────────────────────────

/** How long without a controller signal before it reads as stale (2h). */
export const STALE_CONTROLLER_MS = 2 * 60 * 60 * 1000

/** Why a project's controller icon is showing. `degraded` is the
 *  server-authoritative honesty signal (the controller is gone or its output
 *  is not reaching a durable conversation); the others are client-derived from
 *  the operator override, the controller's own mode, or signal staleness. */
export type ControllerStop =
  | { kind: 'degraded'; reason: 'dropped' | 'misrouted' | 'missing' }
  | { cause: null | string; kind: 'self-stopped' }
  | { cause: null | string; kind: 'waiting' }
  | { kind: 'never-engaged' }
  | { kind: 'operator-paused' }
  | { kind: 'stale'; lastAt: number }

/** Decide WHO/what stopped a project's controller. Server honesty axes
 *  (`controller_health`/`delivery_health`, computed by sandboxed.sh from the
 *  controller link + signal freshness + delivery route) win over the older
 *  client-side derivation; active + reaching-user controllers return null. */
export function controllerStop(project: ProjectRow, now = Date.now()): ControllerStop | null {
  if (project.override === 'paused') {
    return { kind: 'operator-paused' }
  }

  if (project.override) {
    // Archived by the operator — the lifecycle column already says it all.
    return null
  }

  // Server-authoritative: an active project whose engine is gone, or whose
  // output does not reach a durable conversation, is degraded — the honest
  // replacement for a lying "active".
  if (project.controller_health === 'missing') {
    return { kind: 'degraded', reason: 'missing' }
  }

  if (project.delivery_health === 'dropped') {
    return { kind: 'degraded', reason: 'dropped' }
  }

  if (project.delivery_health === 'misrouted') {
    return { kind: 'degraded', reason: 'misrouted' }
  }

  const raw = project.mode ?? project.latest_update?.mode

  if (raw) {
    const [base, ...rest] = raw.trim().toLowerCase().split(':')

    if (base === 'paused') {
      const cause = rest.join(':').trim() || project.latest_update?.blocker?.trim() || null

      return { cause, kind: 'self-stopped' }
    }

    if (base === 'blocked') {
      // Waiting on something external (CI, a node, a scan) is not "stopped".
      // The controller is alive and parked on a blocker — calling that
      // "stopped itself" made Lean Silicon look finished while LSC1-05
      // waited on exact-head CI.
      const cause = rest.join(':').trim() || project.latest_update?.blocker?.trim() || null

      return { cause, kind: 'waiting' }
    }
  }

  const at = project.latest_update?.at
  const lastAt = at ? Date.parse(at) : Number.NaN

  // The freshest proof of life wins: a scheduler heartbeat (the job ran, even
  // if its [SILENT] tick delivered nothing) counts as much as a delivery.
  const heartbeatAt = project.controller_heartbeat_at
    ? Date.parse(project.controller_heartbeat_at)
    : Number.NaN

  const freshestAt =
    Number.isNaN(lastAt) || (!Number.isNaN(heartbeatAt) && heartbeatAt > lastAt) ? heartbeatAt : lastAt

  // The server saw the controller link + heartbeat; when it says healthy, the
  // client's 2h delivery-age heuristic must not overrule it into "stale" — a
  // quiet controller is not a dead one.
  const serverSaysHealthy = project.controller_health === 'healthy'

  if (
    project.controller_health === 'stale' ||
    (!serverSaysHealthy && !Number.isNaN(freshestAt) && now - freshestAt > STALE_CONTROLLER_MS)
  ) {
    return { kind: 'stale', lastAt: freshestAt }
  }

  // Declares a controller but never reported a mode and never posted an update:
  // it never engaged (looks active, nothing runs). No link at all = deliberately
  // unmanaged → no icon.
  if (project.controller_cron_id && !raw && Number.isNaN(lastAt)) {
    return { kind: 'never-engaged' }
  }

  return null
}

// ── card chip selection (pure — unit-tested) ─────────────────────────────────

/** Statuses that count as "live" for the card's chip row, in display order. */
const LIVE_CHIP_ORDER: Record<string, number> = { active: 1, awaiting_user: 0, queued: 2 }

const chipRecency = (chip: MissionChip): number => {
  const ms = chip.updated_at ? Date.parse(chip.updated_at) : Number.NaN

  return Number.isNaN(ms) ? 0 : ms
}

/** Headlines that only restate an auto-resume or a stale writer-lease claim.
 *  When a writer is live these are not the project's current state. */
const STALE_CONTROLLER_HEADLINE =
  /lease writer|bloqu[ée]e par le lease|campagne relanc/i

const LIVE_ITEM_STATUS = new Set([
  'active',
  'created',
  'pending',
  'queued',
  'waiting_background'
])

const CONTROLLER_BEHIND_MS = 15 * 60 * 1000

export function isStaleControllerHeadline(
  headline: string | null | undefined,
  liveCount: number
): boolean {
  if (liveCount < 1 || !headline) {
    return false
  }

  return STALE_CONTROLLER_HEADLINE.test(headline)
}

export type LeadSignal = {
  blocker: string | null
  controllerBehind: boolean
  headline: string | null
  lastSignalAt: string | null
  lastWorkAt: string | null
  liveCount: number
  nextAction: string | null
  pendingDecisions: number
}

/** What the card and the session-right rail should lead with: next action,
 *  live work, pending owner questions — never a lease/relaunch lie while a
 *  writer is running. */
/** A bound-session `/goal` is the project's live objective. Items stay the
 *  checklist; the Ralph loop must not look like a second, ignored roadmap. */
export function sessionGoalLead(
  goal: { status?: null | string; title?: null | string } | null | undefined
): string | null {
  const status = goal?.status?.trim()

  if (status !== 'active' && status !== 'waiting') {
    return null
  }

  const title = goal?.title?.trim()

  return title || null
}

export function leadSignal(project: ProjectRow): LeadSignal {
  const liveCount = project.health?.active ?? 0
  const nextAction = project.next_action?.trim() || null
  const rawHeadline = project.latest_update?.headline?.trim() || null
  const lastSignalAt = project.latest_update?.at ?? project.controller_heartbeat_at ?? null
  const lastWorkAt = latestLiveWorkAt(project)
  const pendingDecisions = project.pending_decisions ?? 0

  const headline = isStaleControllerHeadline(rawHeadline, liveCount)
    ? nextAction
    : (nextAction ?? rawHeadline)

  return {
    blocker: project.latest_update?.blocker?.trim() || null,
    controllerBehind: isControllerBehind(lastSignalAt, lastWorkAt, liveCount),
    headline,
    lastSignalAt,
    lastWorkAt,
    liveCount,
    nextAction,
    pendingDecisions
  }
}

function latestLiveWorkAt(project: ProjectRow): string | null {
  // When the live attempt started — not the last tool heartbeat, and not
  // a track's last_activity_at (that includes finished siblings).
  return latestTimestamp(
    (project.missions ?? [])
      .filter(mission => LIVE_ITEM_STATUS.has(mission.status))
      .map(mission => mission.last_status_change_at || mission.updated_at)
  )
}

function isControllerBehind(
  lastSignalAt: string | null,
  lastWorkAt: string | null,
  liveCount: number
): boolean {
  if (liveCount < 1 || !lastSignalAt || !lastWorkAt) {
    return false
  }

  const signalMs = Date.parse(lastSignalAt)
  const workMs = Date.parse(lastWorkAt)

  return Number.isFinite(signalMs) && Number.isFinite(workMs) && workMs - signalMs > CONTROLLER_BEHIND_MS
}

function latestTimestamp(values: Array<null | string | undefined>): string | null {
  let best: string | null = null
  let bestMs = Number.NEGATIVE_INFINITY

  for (const value of values) {
    if (!value) {
      continue
    }

    const ms = Date.parse(value)

    if (!Number.isFinite(ms) || ms <= bestMs) {
      continue
    }

    bestMs = ms
    best = value
  }

  return best
}

export type RailItem = {
  attempts: ProjectItemAttempt[]
  key: string
  live: boolean
  status: null | string
}

/** Map a project item onto the checklist row the rail/drawer already render.
 *  The backend `/tasks` endpoint now returns this same list — items ARE the
 *  roadmap — so the desktop must not keep a second board-task fetch. */
export function itemAsRoadmapTask(item: ProjectItem): ProjectTask {
  const live = item.attempts.some(attempt => LIVE_ITEM_STATUS.has(attempt.status))

  const failedOnly =
    item.attempts.length > 0 &&
    item.attempts.every(attempt => attempt.status === 'failed' || attempt.status === 'interrupted')

  const status = !item.open
    ? 'accepted'
    : live
      ? 'running'
      : item.status === 'proposed'
        ? 'proposed'
        : failedOnly
          ? 'failed'
          : 'pending'

  const title =
    item.desired_state?.trim() ||
    item.attempts.find(attempt => attempt.title?.trim())?.title?.trim() ||
    item.key

  return {
    attempts: item.attempts.length,
    status,
    task_key: item.key,
    title,
    updated_at: item.attempts[0]?.updated_at ?? null,
    worker_mission_id: item.attempts[0]?.id ?? null
  }
}

/** True when get_project actually sent an items array (including empty). */
export function projectDetailHasItems(detail: ProjectDetail): boolean {
  return Array.isArray(detail.items)
}

export function roadmapFromItems(detail: ProjectDetail): {
  summary: { done: number; failed: number; running: number; total: number }
  tasks: ProjectTask[]
} {
  const tasks = (detail.items ?? [])
    .filter(item => {
      const live = item.attempts.some(attempt => LIVE_ITEM_STATUS.has(attempt.status))

      if (live) {
        return true
      }

      // Older backends omit kind/status; `open` is still authoritative.
      if (item.open) {
        return true
      }

      if (item.kind === 'task') {
        return item.open
      }

      return item.status === 'open' || item.status === 'active' || item.status === 'proposed' || item.status === 'done'
    })
    .map(itemAsRoadmapTask)

  let done = 0
  let running = 0
  let failed = 0

  for (const task of tasks) {
    if (task.status === 'accepted') {
      done += 1
    } else if (task.status === 'running' || task.status === 'settled') {
      running += 1
    } else if (task.status === 'failed') {
      failed += 1
    }
  }

  return { summary: { done, failed, running, total: tasks.length }, tasks }
}

/** Open items, live first, capped so the rail cannot dump a 120-track graveyard. */
export function railOpenItems(detail: ProjectDetail, cap = 8): RailItem[] {
  const ranked = (detail.items ?? [])
    .filter(item => item.open)
    .map(item => {
      const live = item.attempts.some(attempt => LIVE_ITEM_STATUS.has(attempt.status))

      return {
        attempts: item.attempts.slice(0, 2),
        key: item.key,
        live,
        status: item.status ?? null
      }
    })
    .sort((left, right) => Number(right.live) - Number(left.live))

  return ranked.slice(0, cap)
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
