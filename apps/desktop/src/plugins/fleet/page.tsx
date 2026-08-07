/**
 * The Fleet page: your sandboxed.sh projects as a live background-agents
 * surface. Each project shows its mission-agents working, an attention rail for
 * the ones in `awaiting_user`, and an inline composer to steer any of them.
 *
 * Data is React Query over the plugin's `/api/plugins/fleet` router; the board
 * polls every 8s (Phase 7 will swap the poll for a socket-invalidated push).
 */

import { cn, icons, useQuery } from '@hermes/plugin-sdk'
import { useState } from 'react'

import {
  fetchProjects,
  liveMissions,
  type MissionChip,
  needsAttention,
  projectMode,
  type ProjectRow,
  projectsKey,
  steerMission
} from './api'
import { FLEET_LOCALES } from './i18n'

const t = FLEET_LOCALES.en

function ModeChip({ project }: { project: ProjectRow }) {
  const mode = projectMode(project)

  if (!mode) {return null}
  const label = mode.cause ? `${mode.base}: ${mode.cause}` : mode.base

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-wide',
        mode.base === 'blocked' && 'text-amber-400/80',
        mode.base === 'active' && 'text-indigo-300/70',
        mode.base === 'paused' && 'text-white/35'
      )}
      title={label}
    >
      <span
        className={cn(
          'h-1 w-1 shrink-0 rounded-full',
          mode.base === 'blocked' && 'bg-amber-400/80',
          mode.base === 'active' && 'bg-indigo-400/70',
          mode.base === 'paused' && 'bg-white/30'
        )}
      />
      {label}
    </span>
  )
}

function MissionRow({ mission }: { mission: MissionChip }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const attention = mission.status === 'awaiting_user'

  async function send() {
    const content = draft.trim()

    if (!content || !mission.id) {return}
    setSending(true)

    try {
      await steerMission(mission.id, content)
      setDraft('')
      setOpen(false)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5">
      <button
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setOpen(o => !o)}
        type="button"
      >
        <span
          className={cn(
            'h-1.5 w-1.5 shrink-0 rounded-full',
            mission.status === 'active' && 'animate-pulse bg-indigo-400/70',
            attention && 'bg-amber-400/90',
            mission.status === 'failed' && 'bg-red-400/80',
            !['active', 'awaiting_user', 'failed'].includes(mission.status) && 'bg-white/25'
          )}
        />
        <span className="min-w-0 flex-1 truncate text-[12px] text-white/80">
          {mission.title ?? mission.id?.slice(0, 8) ?? 'mission'}
        </span>
        {attention && (
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-amber-400/80">
            needs you
          </span>
        )}
        {mission.github_pr && (
          <span className="shrink-0 text-[10px] text-white/40">{mission.github_pr}</span>
        )}
      </button>
      {open && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <input
            className="min-w-0 flex-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[12px] text-white/85 outline-none placeholder:text-white/30"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {void send()}
            }}
            placeholder={t.steerPlaceholder}
            value={draft}
          />
          <button
            className="shrink-0 rounded bg-indigo-500/80 px-2 py-1 text-[11px] text-white disabled:opacity-40"
            disabled={sending || draft.trim().length === 0}
            onClick={() => void send()}
            type="button"
          >
            {t.steer}
          </button>
        </div>
      )}
    </div>
  )
}

function ProjectCard({ project }: { project: ProjectRow }) {
  const live = liveMissions(project)
  const attention = needsAttention(project)

  // Attention missions first, then the rest of the live ones.
  const ordered = [
    ...attention,
    ...live.filter(m => m.status !== 'awaiting_user')
  ]

  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="truncate text-[13px] font-medium text-white/85">{project.slug}</span>
        <ModeChip project={project} />
        <span className="ml-auto shrink-0 text-[11px] text-white/40">{t.agents(live.length)}</span>
        {attention.length > 0 && (
          <span className="shrink-0 text-[11px] text-amber-400/80">{t.needsYou(attention.length)}</span>
        )}
      </div>
      {ordered.length === 0 ? (
        <p className="text-[11px] text-white/35">
          {project.latest_update?.headline ?? project.attention_reasons[0] ?? '—'}
        </p>
      ) : (
        <div className="space-y-1">
          {ordered.map(m => (
            <MissionRow key={m.id ?? Math.random()} mission={m} />
          ))}
        </div>
      )}
    </div>
  )
}

export function FleetPage() {
  const { data, error, isLoading } = useQuery({
    queryFn: fetchProjects,
    queryKey: projectsKey,
    refetchInterval: 8_000
  })

  // A 503 is "no sandboxed.sh here" — hide the surface rather than alarm.
  const status = (error as { status?: number } | null)?.status

  if (status === 503) {
    return <div className="p-6 text-[12px] text-white/40">{t.empty}</div>
  }

  if (error) {
    return <div className="p-6 text-[12px] text-amber-400/70">{t.unreachable}</div>
  }

  if (isLoading || !data) {
    return <div className="p-6 text-[12px] text-white/35">…</div>
  }

  const projects = data.projects.filter(p => p.bucket !== 'archived')
  const totalLive = projects.reduce((n, p) => n + liveMissions(p).length, 0)
  const totalAttention = projects.reduce((n, p) => n + needsAttention(p).length, 0)

  const FleetIcon = icons.Activity

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-6 py-3">
        <FleetIcon className="h-4 w-4 text-white/60" />
        <span className="text-[13px] font-medium text-white/85">{t.title}</span>
        <span className="ml-2 text-[11px] text-white/40">
          {t.agents(totalLive)}
          {totalAttention > 0 ? ` · ${t.needsYou(totalAttention)}` : ''}
        </span>
      </div>
      <div className="grid flex-1 gap-3 overflow-y-auto p-4 md:grid-cols-2 lg:grid-cols-3">
        {projects.map(p => (
          <ProjectCard key={p.slug} project={p} />
        ))}
      </div>
    </div>
  )
}
