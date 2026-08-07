/**
 * Fleet data layer. Everything goes through `ctx.rest` — the plugin's own
 * `/api/plugins/fleet/*` router (`plugins/fleet/dashboard/plugin_api.py`), which
 * relays to sandboxed.sh through the gateway's credential-free seam. No backend
 * credential ever reaches the renderer.
 *
 * Fetching/caching/polling is React Query's job (via the SDK). This module owns
 * the query keys and the REST calls. A 503 means "no sandboxed.sh on this host"
 * — the surface hides itself; a 502 means the backend is unreachable.
 */

import type { PluginRestOptions } from '@hermes/plugin-sdk'

export type MissionStatus = string

export interface MissionChip {
  id: string | null
  status: MissionStatus
  title: string | null
  updated_at: string | null
  github_pr: string | null
}

export interface ProjectHealth {
  missions: number
  active: number
  failed: number
  overdue: number
  tracks_needing_attention: number
}

export interface DeliveryUpdate {
  headline: string
  at: string | null
  mode: string | null
  blocker: string | null
}

export interface ProjectRow {
  slug: string
  bucket: string
  missions: MissionChip[]
  latest_update: DeliveryUpdate | null
  attention_reasons: string[]
  health?: ProjectHealth
}

export interface ProjectsResponse {
  projects: ProjectRow[]
}

export interface ProjectRecord {
  slug: string
  title?: string | null
  objective?: string | null
  status: string
  mode?: string | null
  wait_ticks: number
  next_action?: string | null
  blocker?: string | null
  controller_cron_id?: string | null
}

export interface ProjectGrant {
  merge_authority?: string | null
  budget_per_tick?: string | null
  pause_reason?: string | null
  resume_condition?: string | null
  material_bar?: string | null
}

export interface ProjectDetail {
  project: ProjectRecord
  grant?: ProjectGrant | null
  tracks?: Array<{ track: string; desired_state?: string | null; status?: string | null }>
  open_decisions?: Array<{ at: string; question: string; rationale?: string | null }>
  conversation?: { session_id: string; source: string } | null
}

type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>

let rest: null | Rest = null

/** Wire the plugin's REST door in from `plugin.register(ctx)`. */
export function bindApi(restFn: Rest): () => void {
  rest = restFn

  return () => {
    rest = null
  }
}

function requireRest(): Rest {
  if (!rest) {
    throw new Error('fleet: API used before bindApi()')
  }

  return rest
}

export const projectsKey = ['fleet', 'projects'] as const
export const projectKey = (slug: string) => ['fleet', 'project', slug] as const

export async function fetchProjects(): Promise<ProjectsResponse> {
  return requireRest()<ProjectsResponse>('/projects')
}

export async function fetchProject(slug: string): Promise<ProjectDetail> {
  return requireRest()<ProjectDetail>(`/projects/${encodeURIComponent(slug)}`)
}

export async function steerMission(missionId: string, content: string): Promise<void> {
  await requireRest()(`/missions/${encodeURIComponent(missionId)}/message`, {
    method: 'POST',
    body: { content }
  })
}

/** Live missions in a project — the "agents" of the background-agents surface. */
export function liveMissions(project: ProjectRow): MissionChip[] {
  return project.missions.filter(m => m.status === 'active' || m.status === 'awaiting_user')
}

/** Missions that need the operator — the attention rail. */
export function needsAttention(project: ProjectRow): MissionChip[] {
  return project.missions.filter(m => m.status === 'awaiting_user')
}

/** The controller mode from the latest delivery, split into base + cause. */
export function projectMode(project: ProjectRow): { base: string; cause: string | null } | null {
  const raw = project.latest_update?.mode

  if (!raw) {return null}
  const [base, ...rest] = raw.trim().toLowerCase().split(':')

  if (base !== 'active' && base !== 'blocked' && base !== 'paused') {return null}
  const cause = rest.join(':').trim()

  return { base, cause: cause.length > 0 ? cause : null }
}
