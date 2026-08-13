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
  answerDecision,
  type AutonomyLevel,
  fetchProject,
  fetchProjectState,
  fetchProjectTasks,
  isBoundConversation,
  type MissionChip,
  type ProjectDecision,
  type ProjectGrant,
  projectKey,
  type ProjectRow,
  PROJECTS_KEY,
  type ProjectTask,
  saveGrant,
  stateKey,
  tasksKey,
  type TrackHealth
} from './api'
import { ago, errText } from './board'
import { SteerInput } from './color-swatches'
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

// ── autonomy chip ────────────────────────────────────────────────────────────

const AUTONOMY_TONE: Record<AutonomyLevel, string> = {
  act_full: '#818cf8',
  act_reversible: '#818cf8',
  observe: 'var(--ui-text-tertiary)',
  propose: '#fbbf24'
}

export const AUTONOMY_LEVELS: AutonomyLevel[] = ['observe', 'propose', 'act_reversible', 'act_full']

/** The grant's normalized level as a compact chip. Hidden when unset (older
 *  backend or ungoverned project) — absence must not read as "observes". */
export function AutonomyChip({ level }: { level?: AutonomyLevel | null }) {
  const b = useBoard()

  if (!level || !(level in AUTONOMY_TONE)) {
    return null
  }

  const tone = AUTONOMY_TONE[level]

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-px text-[0.5625rem] uppercase tracking-wide"
      style={{ backgroundColor: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}
      title={b.autonomyTip[level]}
    >
      <Codicon name="shield" size="0.65rem" />
      {b.autonomy[level]}
    </span>
  )
}

// ── needs-you: pending decisions, answerable inline ──────────────────────────

function PendingDecisionRow({ decision, slug }: { decision: ProjectDecision; slug: string }) {
  const b = useBoard()
  const qc = useQueryClient()
  const [answer, setAnswer] = useState('')

  const mut = useMutation({
    mutationFn: () => answerDecision(slug, decision.at, answer.trim(), decision.question),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSuccess: result => {
      host.notify({ kind: 'info', message: result.injected ? b.answerDelivered : b.answerRecorded })
      void qc.invalidateQueries({ queryKey: projectKey(slug) })
      void qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    }
  })

  return (
    <div className="flex flex-col gap-1 rounded-md border border-amber-400/25 bg-(--ui-bg-quinary) px-2.5 py-2 text-[0.71rem]">
      <div className="flex items-baseline gap-2">
        {decision.kind && (
          <span className="shrink-0 rounded bg-(--ui-bg-quaternary) px-1 text-[0.5625rem] uppercase tracking-wide text-(--ui-text-tertiary)">
            {decision.kind}
          </span>
        )}
        <span className="min-w-0 flex-1 text-(--ui-text-secondary)">{decision.question}</span>
        {ago(decision.at) && (
          <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{ago(decision.at)}</span>
        )}
      </div>
      {decision.rationale && <div className="text-(--ui-text-quaternary)">{decision.rationale}</div>}
      <form
        className="mt-0.5 flex items-center gap-1.5"
        onSubmit={event => {
          event.preventDefault()

          if (answer.trim() && !mut.isPending) {
            mut.mutate()
          }
        }}
      >
        <Input
          className="h-6 flex-1 text-[0.6875rem]"
          onChange={event => setAnswer(event.target.value)}
          placeholder={b.answerPlaceholder}
          value={answer}
        />
        <button
          className="shrink-0 rounded bg-primary/80 px-2 py-0.5 text-[0.625rem] text-primary-foreground transition-opacity disabled:opacity-40"
          disabled={!answer.trim() || mut.isPending}
          type="submit"
        >
          {b.answerSend}
        </button>
      </form>
    </div>
  )
}

// ── roadmap checklist ────────────────────────────────────────────────────────

/** Task status → checklist glyph + tone. `settled` is "done, awaiting the
 *  boss's verdict" — rendered as done-ish but muted. */
const TASK_GLYPH: Record<string, { icon: string; tone: string }> = {
  accepted: { icon: 'pass-filled', tone: '#34d399' },
  cancelled: { icon: 'circle-slash', tone: 'var(--ui-text-quaternary)' },
  failed: { icon: 'error', tone: '#f87171' },
  pending: { icon: 'circle-large-outline', tone: 'var(--ui-text-quaternary)' },
  running: { icon: 'play-circle', tone: '#818cf8' },
  settled: { icon: 'pass', tone: '#34d399' }
}

function TaskRow({ task }: { task: ProjectTask }) {
  const b = useBoard()
  const [open, setOpen] = useState(false)
  const glyph = TASK_GLYPH[task.status] ?? TASK_GLYPH.pending

  const expandable = Boolean(
    task.result_digest || task.pr_url || task.worker_mission_id || task.acceptance_criteria?.length
  )

  return (
    <div className="rounded-md text-[0.71rem]">
      <button
        className={cn('flex w-full items-center gap-2 rounded px-1 py-0.5 text-left', expandable && 'hover:bg-(--ui-bg-quinary)')}
        onClick={() => expandable && setOpen(o => !o)}
        type="button"
      >
        <Codicon className="shrink-0" name={glyph.icon} size="0.8rem" style={{ color: glyph.tone }} />
        <span
          className={cn(
            'min-w-0 flex-1 truncate',
            task.status === 'accepted' ? 'text-(--ui-text-quaternary) line-through decoration-(--ui-stroke-secondary)' : 'text-(--ui-text-secondary)'
          )}
        >
          {task.title}
        </span>
        {task.pr_url && <Codicon className="shrink-0 text-(--ui-text-quaternary)" name="git-pull-request" size="0.7rem" />}
        {(task.attempts ?? 0) > 1 && (
          <span className="shrink-0 text-[0.5625rem] text-amber-500">{b.taskAttempts(task.attempts!)}</span>
        )}
        {expandable && (
          <Codicon className="shrink-0 text-(--ui-text-quaternary)" name={open ? 'chevron-up' : 'chevron-down'} size="0.7rem" />
        )}
      </button>
      {open && (
        <div className="mb-1 ml-6 flex flex-col gap-1 rounded-md bg-(--ui-bg-quinary) px-2 py-1.5">
          {task.result_digest && (
            <p className="whitespace-pre-wrap break-words text-[0.6875rem] leading-relaxed text-(--ui-text-secondary)">
              {task.result_digest}
            </p>
          )}
          {(task.acceptance_criteria?.length ?? 0) > 0 && (
            <div className="text-[0.625rem] text-(--ui-text-quaternary)">
              {b.acceptanceCriteria}: {task.acceptance_criteria!.join(' · ')}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {task.pr_url && (
              <a
                className="inline-flex items-center gap-1 text-[0.6875rem] text-indigo-400 hover:underline"
                href={task.pr_url}
                rel="noreferrer"
                target="_blank"
              >
                <Codicon name="git-pull-request" size="0.7rem" />
                {b.openPr}
              </a>
            )}
            {task.worker_mission_id && (
              <span className="font-mono text-[0.625rem] text-(--ui-text-quaternary)">
                {b.workerMission} {task.worker_mission_id.slice(0, 8)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── recent activity: the controller's declared decisions ─────────────────────

function ActivityRow({ decision }: { decision: ProjectDecision }) {
  const b = useBoard()
  const answered = decision.status === 'answered'
  const pr = decision.evidence?.pr_url

  return (
    <div className="flex flex-col gap-0.5 text-[0.71rem]">
      <div className="flex items-baseline gap-2">
        <span
          className="shrink-0 rounded bg-(--ui-bg-quaternary) px-1 text-[0.5625rem] uppercase tracking-wide"
          style={{ color: answered ? '#fbbf24' : 'var(--ui-text-tertiary)' }}
        >
          {decision.kind || (answered ? b.answeredLabel : b.decidedLabel)}
        </span>
        <span className="min-w-0 flex-1 text-(--ui-text-secondary)">{decision.question}</span>
        {ago(decision.at) && (
          <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{ago(decision.at)}</span>
        )}
      </div>
      {decision.rationale && <div className="pl-0.5 text-(--ui-text-quaternary)">{decision.rationale}</div>}
      {answered && decision.answer && (
        <div className="pl-0.5 text-(--ui-text-tertiary)">→ {decision.answer}</div>
      )}
      {pr && (
        <a
          className="inline-flex w-fit items-center gap-1 text-[0.6875rem] text-indigo-400 hover:underline"
          href={pr}
          rel="noreferrer"
          target="_blank"
        >
          <Codicon name="git-pull-request" size="0.7rem" />
          {pr.split('/').slice(-3).join('/')}
        </a>
      )}
    </div>
  )
}

/** A collapsed-by-default section for secondary content. */
function Collapsed({ children, count, label }: { children: ReactNode; count?: number; label: string }) {
  const [open, setOpen] = useState(false)

  return (
    <section className="flex flex-col gap-1.5">
      <button className="flex items-center gap-1.5 text-left" onClick={() => setOpen(o => !o)} type="button">
        <Codicon className="text-(--ui-text-quaternary)" name={open ? 'chevron-down' : 'chevron-right'} size="0.7rem" />
        <span className={FIELD_LABEL}>{label}</span>
        {count != null && count > 0 && (
          <span className="text-[0.625rem] tabular-nums text-(--ui-text-quaternary)">{count}</span>
        )}
      </button>
      {open && children}
    </section>
  )
}

// ── grant panel ──────────────────────────────────────────────────────────────

function GrantPanel({ grant, slug }: { grant: null | ProjectGrant | undefined; slug: string }) {
  const b = useBoard()
  const qc = useQueryClient()
  const [mergeAuthority, setMergeAuthority] = useState('')
  const [budget, setBudget] = useState('')
  const [parallel, setParallel] = useState('')
  const [level, setLevel] = useState('')

  // Re-seed the fields whenever a (new) grant arrives.
  useEffect(() => {
    setMergeAuthority(grant?.merge_authority ?? '')
    setBudget(grant?.budget_per_tick ?? '')
    setParallel(grant?.parallel_missions != null ? String(grant.parallel_missions) : '')
    setLevel(grant?.autonomy_level ?? '')
  }, [grant])

  const mut = useMutation({
    mutationFn: () =>
      saveGrant(slug, {
        autonomy_level: level || null,
        budget_per_tick: budget.trim() || null,
        merge_authority: mergeAuthority.trim() || null,
        parallel_missions: parallel.trim() ? Number(parallel) : null
      }),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSuccess: () => {
      host.notify({ kind: 'info', message: b.grantSaved })
      void qc.invalidateQueries({ queryKey: projectKey(slug) })
      void qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    }
  })

  const dirty =
    mergeAuthority !== (grant?.merge_authority ?? '') ||
    budget !== (grant?.budget_per_tick ?? '') ||
    parallel !== (grant?.parallel_missions != null ? String(grant.parallel_missions) : '') ||
    level !== (grant?.autonomy_level ?? '')

  return (
    <Section label={b.grant}>
      <span className="text-[0.625rem] text-(--ui-text-quaternary)">{b.grantHint}</span>
      <div className="grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 text-[0.71rem]">
        <span className="text-(--ui-text-quaternary)">{b.autonomyLevel}</span>
        <select
          className="h-6 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-1.5 text-[0.6875rem] text-(--ui-text-secondary)"
          onChange={event => setLevel(event.target.value)}
          value={level}
        >
          <option value="">{b.autonomyLevelUnset}</option>
          {AUTONOMY_LEVELS.map(name => (
            <option key={name} value={name}>
              {b.autonomy[name]}
            </option>
          ))}
        </select>
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
  const attention = mission.status === 'awaiting_user'

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
        <div className="mt-1.5">
          <SteerInput missionId={mission.id} onDone={() => setOpen(false)} />
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

  const { data: roadmap, error: roadmapError } = useQuery({
    enabled: Boolean(slug),
    queryFn: () => fetchProjectTasks(slug!),
    queryKey: tasksKey(slug ?? '')
  })

  // An older backend/relay without the endpoint (404, or 503 = no sandboxed.sh)
  // legitimately has no roadmap — stay silent. Any other failure is a transient
  // outage and must not masquerade as "empty roadmap".
  const roadmapStatus = (roadmapError as { status?: number } | null)?.status
  const roadmapUnavailable = Boolean(roadmapError) && roadmapStatus !== 404 && roadmapStatus !== 503

  if (!slug) {
    return null
  }

  const project = detail?.project
  const conversation = detail?.conversation ?? row?.conversation ?? null
  const tracks = detail?.tracks ?? row?.health?.tracks ?? []
  const errorMessage = error ? errText(error) : null
  const pending = detail?.open_decisions ?? []
  const activity = detail?.recent_decisions ?? []
  const tasks = roadmap?.tasks ?? []
  const summary = roadmap?.summary

  const autonomyLevel = (detail?.grant?.autonomy_level ?? row?.autonomy_level) as
    | AutonomyLevel
    | null
    | undefined

  return (
    <Dialog onOpenChange={open => !open && onClose()} open>
      <DialogContent className="w-[min(38rem,94vw)] max-w-none">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {project?.title || slug}
            <AutonomyChip level={autonomyLevel} />
          </DialogTitle>
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

              {isBoundConversation(conversation) && (
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

              {/* Needs you — first, because it is the reason to open the card. */}
              {pending.length > 0 && (
                <Section label={b.needsYouSection}>
                  <div className="flex flex-col gap-1.5">
                    {pending.map(decision => (
                      <PendingDecisionRow decision={decision} key={decision.at} slug={slug} />
                    ))}
                  </div>
                </Section>
              )}

              {/* Roadmap — the project's checklist, from the task board. */}
              {roadmapUnavailable && (
                <Section label={b.roadmap}>
                  <span className="text-[0.71rem] text-amber-500">{b.roadmapUnavailable}</span>
                </Section>
              )}
              {tasks.length > 0 && (
                <Section
                  label={
                    summary && summary.total > 0
                      ? `${b.roadmap} · ${b.roadmapProgress(summary.done, summary.total)}`
                      : b.roadmap
                  }
                >
                  <div className="flex flex-col gap-0.5">
                    {tasks.map((task, index) => (
                      <TaskRow key={task.id ?? `${task.boss_mission_id}-${task.task_key}-${index}`} task={task} />
                    ))}
                  </div>
                </Section>
              )}

              {/* Recent activity — the controller's declared decisions. */}
              {activity.length > 0 && (
                <Section label={b.recentActivity}>
                  <div className="flex flex-col gap-2">
                    {activity.map(decision => (
                      <ActivityRow decision={decision} key={decision.at} />
                    ))}
                  </div>
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

              {(row?.missions.length ?? 0) > 0 && (
                <Collapsed count={row!.missions.length} label={b.liveMissions}>
                  <div className="flex flex-col gap-1">
                    {row!.missions.map((mission, index) => (
                      <MissionRow key={mission.id ?? index} mission={mission} />
                    ))}
                  </div>
                </Collapsed>
              )}

              {/* Debug — the raw scanner/state timeline, demoted from the main view. */}
              {(timeline?.states.length ?? 0) > 0 && (
                <Collapsed count={timeline!.states.length} label={`${b.debugSection} · ${b.stateTimeline}`}>
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
                </Collapsed>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
