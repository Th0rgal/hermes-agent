/**
 * Project drawer — a Dialog opened from a board card. Shows the project's
 * structured object (objective / next action / blocker / controller cron /
 * repository), tracks with verdict badges, the editable autonomy grant, open
 * decisions, the state timeline, mission rows with inline steering, and the
 * bound control conversation (click-through to the session).
 */

import {
  Badge,
  cn,
  Codicon,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  ErrorState,
  host,
  Input,
  Loader,
  useMutation,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useState } from 'react'

import {
  fetchProject,
  fetchProjectState,
  type MissionChip,
  type ProjectGrant,
  projectKey,
  type ProjectRow,
  saveGrant,
  stateKey,
  steerMission,
  type TrackHealth
} from './api'
import { ago, errText } from './board'
import { useBoard } from './i18n'

const FIELD_LABEL = 'text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-(--ui-text-quaternary)'

function Section({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section className="flex flex-col gap-1.5">
      <div className={FIELD_LABEL}>{label}</div>
      {children}
    </section>
  )
}

function MetaRow({ children, label }: { children: ReactNode; label: string }) {
  return (
    <>
      <span className="text-(--ui-text-quaternary)">{label}</span>
      <span className="min-w-0 break-words text-(--ui-text-secondary)">{children}</span>
    </>
  )
}

// Track status → badge tone. Unknown verdicts render neutral.
const TRACK_TONE: Record<string, string> = {
  attention: '#fbbf24',
  blocked: '#fbbf24',
  done: '#34d399',
  failed: '#f87171',
  green: '#34d399',
  ok: '#34d399',
  red: '#f87171'
}

function TrackRow({ track }: { track: TrackHealth }) {
  const verdict = (track.status ?? '').toLowerCase()
  const tone = TRACK_TONE[verdict] ?? 'var(--ui-text-tertiary)'
  const when = ago(track.updated_at)

  return (
    <div className="flex items-center gap-2 text-[0.71rem]">
      <span className="min-w-0 flex-1 truncate text-(--ui-text-secondary)">{track.track}</span>
      {track.desired_state && (
        <span className="min-w-0 truncate text-(--ui-text-quaternary)">→ {track.desired_state}</span>
      )}
      {track.status && (
        <Badge
          className="shrink-0"
          style={{ backgroundColor: `color-mix(in srgb, ${tone} 15%, transparent)`, color: tone }}
          variant="muted"
        >
          {track.status}
        </Badge>
      )}
      {when && <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{when}</span>}
    </div>
  )
}

// ── grant panel ──────────────────────────────────────────────────────────────

function GrantPanel({ grant, slug }: { grant: null | ProjectGrant | undefined; slug: string }) {
  const b = useBoard()
  const qc = useQueryClient()
  const [mergeAuthority, setMergeAuthority] = useState('')
  const [budget, setBudget] = useState('')
  const [parallel, setParallel] = useState('')

  // Re-seed the fields whenever a (new) grant arrives.
  useEffect(() => {
    setMergeAuthority(grant?.merge_authority ?? '')
    setBudget(grant?.budget_per_tick ?? '')
    setParallel(grant?.parallel_missions != null ? String(grant.parallel_missions) : '')
  }, [grant])

  const mut = useMutation({
    mutationFn: () =>
      saveGrant(slug, {
        budget_per_tick: budget.trim() || null,
        merge_authority: mergeAuthority.trim() || null,
        parallel_missions: parallel.trim() ? Number(parallel) : null
      }),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSuccess: () => {
      host.notify({ kind: 'info', message: b.grantSaved })
      void qc.invalidateQueries({ queryKey: projectKey(slug) })
    }
  })

  const dirty =
    mergeAuthority !== (grant?.merge_authority ?? '') ||
    budget !== (grant?.budget_per_tick ?? '') ||
    parallel !== (grant?.parallel_missions != null ? String(grant.parallel_missions) : '')

  return (
    <Section label={b.grant}>
      <span className="text-[0.625rem] text-(--ui-text-quaternary)">{b.grantHint}</span>
      <div className="grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 text-[0.71rem]">
        <span className="text-(--ui-text-quaternary)">{b.mergeAuthority}</span>
        <Input onChange={event => setMergeAuthority(event.target.value)} value={mergeAuthority} />
        <span className="text-(--ui-text-quaternary)">{b.budgetPerTick}</span>
        <Input onChange={event => setBudget(event.target.value)} value={budget} />
        <span className="text-(--ui-text-quaternary)">{b.parallelMissions}</span>
        <Input min={0} onChange={event => setParallel(event.target.value)} type="number" value={parallel} />
      </div>
      {(grant?.pause_reason || grant?.resume_condition || grant?.material_bar) && (
        <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-[0.71rem]">
          {grant?.pause_reason && <MetaRow label={b.pauseReason}>{grant.pause_reason}</MetaRow>}
          {grant?.resume_condition && <MetaRow label={b.resumeCondition}>{grant.resume_condition}</MetaRow>}
          {grant?.material_bar && <MetaRow label={b.materialBar}>{grant.material_bar}</MetaRow>}
        </div>
      )}
      <button
        className="self-start rounded bg-primary/80 px-2 py-1 text-[0.6875rem] text-primary-foreground transition-opacity disabled:opacity-40"
        disabled={!dirty || mut.isPending}
        onClick={() => mut.mutate()}
        type="button"
      >
        {b.saveGrant}
      </button>
    </Section>
  )
}

// ── mission row with inline steering ─────────────────────────────────────────

function MissionRow({ mission }: { mission: MissionChip }) {
  const b = useBoard()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const attention = mission.status === 'awaiting_user'

  const send = useMutation({
    mutationFn: () => steerMission(mission.id!, draft.trim()),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSuccess: () => {
      setDraft('')
      setOpen(false)
      host.notify({ kind: 'info', message: b.sent })
    }
  })

  return (
    <div className="rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-2.5 py-1.5">
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(o => !o)} type="button">
        <span
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            mission.status === 'active' && 'animate-pulse bg-indigo-400/80',
            attention && 'bg-amber-400/90',
            mission.status === 'failed' && 'bg-destructive',
            !['active', 'awaiting_user', 'failed'].includes(mission.status) && 'bg-(--ui-text-quaternary)'
          )}
        />
        <span className="min-w-0 flex-1 truncate text-[0.75rem] text-(--ui-text-secondary)">
          {mission.title ?? mission.id?.slice(0, 8) ?? 'mission'}
        </span>
        {attention && (
          <span className="shrink-0 text-[0.625rem] uppercase tracking-wide text-amber-500">{b.needsYouTag}</span>
        )}
        {mission.github_pr && (
          <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{mission.github_pr}</span>
        )}
      </button>
      {open && mission.id && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <Input
            className="h-6 flex-1 text-[0.75rem]"
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && draft.trim() && !send.isPending) {
                send.mutate()
              }
            }}
            placeholder={b.steerPlaceholder}
            value={draft}
          />
          <button
            className="shrink-0 rounded bg-primary/80 px-2 py-1 text-[0.6875rem] text-primary-foreground disabled:opacity-40"
            disabled={send.isPending || draft.trim().length === 0}
            onClick={() => send.mutate()}
            type="button"
          >
            {b.steer}
          </button>
        </div>
      )}
    </div>
  )
}

// ── drawer ───────────────────────────────────────────────────────────────────

export function ProjectDrawer({
  onClose,
  row,
  slug
}: {
  onClose: () => void
  row: null | ProjectRow
  slug: null | string
}) {
  const b = useBoard()

  const { data: detail, error } = useQuery({
    enabled: Boolean(slug),
    queryFn: () => fetchProject(slug!),
    queryKey: projectKey(slug ?? '')
  })

  const { data: timeline } = useQuery({
    enabled: Boolean(slug),
    queryFn: () => fetchProjectState(slug!),
    queryKey: stateKey(slug ?? '')
  })

  if (!slug) {
    return null
  }

  const project = detail?.project
  const conversation = detail?.conversation ?? row?.conversation ?? null
  const tracks = detail?.tracks ?? row?.health?.tracks ?? []
  const errorMessage = error ? errText(error) : null

  return (
    <Dialog onOpenChange={open => !open && onClose()} open>
      <DialogContent className="w-[min(38rem,94vw)] max-w-none">
        <DialogHeader>
          <DialogTitle>{project?.title || slug}</DialogTitle>
        </DialogHeader>
        <div className="flex max-h-[min(72vh,44rem)] flex-col gap-4 overflow-y-auto pr-0.5 text-sm" data-selectable-text="true">
          {errorMessage ? (
            <ErrorState title={errorMessage} />
          ) : !detail || !project ? (
            <div className="grid h-32 place-items-center">
              <Loader type="lemniscate-bloom" />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-[0.71rem]">
                {project.objective && <MetaRow label={b.objective}>{project.objective}</MetaRow>}
                <MetaRow label={b.status}>
                  {project.status}
                  {project.mode ? ` · ${project.mode}` : ''}
                </MetaRow>
                {project.next_action && <MetaRow label={b.nextAction}>{project.next_action}</MetaRow>}
                {project.blocker && (
                  <MetaRow label={b.blocker}>
                    <span className="text-amber-500">{project.blocker}</span>
                  </MetaRow>
                )}
                {project.controller_cron_id && (
                  <MetaRow label={b.controllerCron}>
                    <span className="font-mono">{project.controller_cron_id}</span>
                  </MetaRow>
                )}
                {project.repository && <MetaRow label={b.repository}>{project.repository}</MetaRow>}
              </div>

              {conversation?.session_id && (
                <Section label={b.conversation}>
                  <button
                    className="flex items-center gap-1.5 self-start rounded border border-(--ui-stroke-tertiary) px-2 py-1 text-[0.71rem] text-(--ui-text-secondary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground"
                    onClick={() => {
                      onClose()
                      // A chat session's route is `/<encoded session id>`
                      // (routes.ts sessionRoute) — same shape, no core import.
                      host.navigate(`/${encodeURIComponent(conversation.session_id)}`)
                    }}
                    type="button"
                  >
                    <Codicon name="comment-discussion" size="0.8rem" />
                    {b.openConversation}
                    <span className="font-mono text-(--ui-text-quaternary)">{conversation.session_id.slice(0, 8)}</span>
                  </button>
                </Section>
              )}

              {(row?.missions.length ?? 0) > 0 ? (
                <Section label={b.liveMissions}>
                  <div className="flex flex-col gap-1">
                    {row!.missions.map((mission, index) => (
                      <MissionRow key={mission.id ?? index} mission={mission} />
                    ))}
                  </div>
                </Section>
              ) : (
                <Section label={b.liveMissions}>
                  <span className="text-[0.71rem] text-(--ui-text-quaternary)">{b.noMissions}</span>
                </Section>
              )}

              {tracks.length > 0 && (
                <Section label={b.tracks}>
                  <div className="flex flex-col gap-1">
                    {tracks.map(track => (
                      <TrackRow key={track.track} track={track} />
                    ))}
                  </div>
                </Section>
              )}

              <GrantPanel grant={detail.grant} slug={slug} />

              {(detail.open_decisions?.length ?? 0) > 0 && (
                <Section label={b.openDecisions}>
                  <div className="flex flex-col gap-1.5">
                    {detail.open_decisions!.map((decision, index) => (
                      <div className="text-[0.71rem]" key={index}>
                        <div className="text-(--ui-text-secondary)">{decision.question}</div>
                        {decision.rationale && (
                          <div className="text-(--ui-text-quaternary)">{decision.rationale}</div>
                        )}
                        {ago(decision.at) && (
                          <div className="text-[0.625rem] text-(--ui-text-quaternary)">{ago(decision.at)}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {(timeline?.states.length ?? 0) > 0 && (
                <Section label={b.stateTimeline}>
                  <div className="flex flex-col gap-1.5">
                    {timeline!.states.map(state => {
                      const observations = Array.isArray(state.observations)
                        ? state.observations.length
                        : typeof state.observations === 'number'
                          ? state.observations
                          : 0

                      return (
                        <div className="flex items-baseline gap-2 text-[0.71rem]" key={state.signature}>
                          <span className="min-w-0 flex-1 text-(--ui-text-secondary)">{state.headline}</span>
                          {observations > 0 && (
                            <span className="shrink-0 text-(--ui-text-quaternary)">{b.observations(observations)}</span>
                          )}
                          <span className="shrink-0 font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                            {state.signature.slice(0, 8)}
                          </span>
                          {ago(state.last_seen_at ?? state.first_seen_at) && (
                            <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">
                              {ago(state.last_seen_at ?? state.first_seen_at)}
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
