/**
 * Project rail — contributed into CHAT_RAIL_AREA. When the open chat session
 * is bound to a project, the chat view *becomes* the project view: the rail
 * shows the project around the conversation (needs-you, roadmap, recent
 * activity, grant summary) instead of embedding a chat inside a project page.
 *
 * Renders null when the open session is not bound to any project, so every
 * other conversation keeps the full-width chat. Collapsible to a small chip
 * (persisted): the project stays one click away without imposing the panel.
 */

import {
  $activeChatSessionIds,
  $goalsBySession,
  $projectBoundSessionIds,
  cn,
  Codicon,
  Tip,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import {
  $railCollapsed,
  fetchProject,
  fetchProjects,
  fetchProjectTasks,
  leadSignal,
  projectDetailHasItems,
  projectKey,
  PROJECTS_KEY,
  roadmapFromItems,
  sessionGoalLead,
  tasksKey
} from './api'
import {
  ActivityRow,
  AutonomyChip,
  grantSummaryParts,
  PendingDecisionRow,
  ProjectDrawer,
  Section,
  TaskRow
} from './drawer'
import { useBoard } from './i18n'

/** The slug bound to the open chat session, resolved through the published
 *  bindings (which already cover stored AND live continuation ids). */
function useActiveProjectSlug(): null | string {
  const activeIds = useValue($activeChatSessionIds)
  const bindings = useValue($projectBoundSessionIds)

  for (const id of activeIds) {
    const slug = bindings[id]

    if (slug) {
      return slug
    }
  }

  return null
}

function RailBody({ slug }: { slug: string }) {
  const b = useBoard()
  const [cardOpen, setCardOpen] = useState(false)
  const activeIds = useValue($activeChatSessionIds)
  const goals = useValue($goalsBySession)
  const sessionObjective = activeIds.map(id => sessionGoalLead(goals[id])).find(Boolean) ?? null

  const { data: detail } = useQuery({
    queryFn: () => fetchProject(slug),
    queryKey: projectKey(slug),
    refetchInterval: 60_000,
    retry: false
  })

  const { data: tasksFallback } = useQuery({
    enabled: Boolean(detail) && !projectDetailHasItems(detail!),
    queryFn: () => fetchProjectTasks(slug),
    queryKey: tasksKey(slug),
    retry: false
  })

  const { data: board } = useQuery({
    queryFn: fetchProjects,
    queryKey: PROJECTS_KEY,
    refetchInterval: 60_000,
    retry: false
  })

  const project = detail?.project
  const pending = detail?.open_decisions ?? []
  const activity = (detail?.recent_decisions ?? []).slice(0, 5)

  const roadmap = detail
    ? projectDetailHasItems(detail)
      ? roadmapFromItems(detail)
      : tasksFallback
        ? { summary: tasksFallback.summary ?? { done: 0, failed: 0, running: 0, total: tasksFallback.tasks.length }, tasks: tasksFallback.tasks }
        : null
    : null

  const tasks = roadmap?.tasks ?? []
  const summary = roadmap?.summary
  const row = board?.projects.find(candidate => candidate.slug === slug)
  const signal = row ? leadSignal(row) : null

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-[0.8125rem] font-medium text-foreground">
          {project?.title || slug}
        </span>
        <AutonomyChip level={detail?.grant?.autonomy_level} />
        <Tip label={b.openCard}>
          <button
            className="grid size-5 place-items-center rounded text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground"
            onClick={() => setCardOpen(true)}
            type="button"
          >
            <Codicon name="project" size="0.8rem" />
          </button>
        </Tip>
        <Tip label={b.railHide}>
          <button
            className="grid size-5 place-items-center rounded text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground"
            onClick={() => $railCollapsed.set(true)}
            type="button"
          >
            <Codicon name="chevron-right" size="0.8rem" />
          </button>
        </Tip>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-3" data-selectable-text="true">
        {(signal?.nextAction || signal?.controllerBehind || project?.mode) && (
          <div className="flex flex-col gap-1 rounded-md border border-(--ui-stroke-tertiary) px-2.5 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-quaternary)">
              {project?.mode && <span className="uppercase tracking-wide">{project.mode}</span>}
              {signal?.controllerBehind && (
                <span className="text-amber-500">{b.controllerBehind}</span>
              )}
              {signal?.lastSignalAt && (
                <span className="ml-auto">{b.lastSignal}</span>
              )}
            </div>
            {signal?.nextAction && (
              <div className="text-[0.71rem] text-(--ui-text-secondary)">
                {b.nextActionArrow(signal.nextAction)}
              </div>
            )}
          </div>
        )}

        {sessionObjective && (
          <Section label={b.sessionGoal}>
            <div className="text-[0.71rem] text-(--ui-text-secondary)">{sessionObjective}</div>
          </Section>
        )}

        {project?.blocker && (
          <div className="rounded-md border border-amber-400/25 bg-amber-400/10 px-2.5 py-1.5 text-[0.71rem] text-amber-500">
            {project.blocker}
          </div>
        )}

        {pending.length > 0 && (
          <Section label={b.needsYouSection}>
            <div className="flex flex-col gap-1.5">
              {pending.map(decision => (
                <PendingDecisionRow decision={decision} key={decision.at} slug={slug} />
              ))}
            </div>
          </Section>
        )}

        <Section
          label={
            summary && summary.total > 0
              ? `${b.roadmap} · ${b.roadmapProgress(summary.done, summary.total)}`
              : b.roadmap
          }
        >
          {tasks.length === 0 ? (
            <span className="text-[0.71rem] text-(--ui-text-quaternary)">{b.roadmapEmpty}</span>
          ) : (
            <div className="flex flex-col gap-0.5">
              {tasks.map((task, index) => (
                <TaskRow key={task.task_key || `${index}`} task={task} />
              ))}
            </div>
          )}
        </Section>

        {activity.length > 0 && (
          <Section label={b.recentActivity}>
            <div className="flex flex-col gap-2">
              {activity.map(decision => (
                <ActivityRow decision={decision} key={decision.at} />
              ))}
            </div>
          </Section>
        )}

        {detail && (
          <Section label={b.grant}>
            <span className="text-[0.71rem] text-(--ui-text-tertiary)">
              {grantSummaryParts(b, detail.grant).join(' · ')}
            </span>
          </Section>
        )}

        {project?.next_action && (
          <div className="text-[0.6875rem] text-(--ui-text-quaternary)">
            {b.nextActionArrow(project.next_action)}
          </div>
        )}
      </div>

      {cardOpen && <ProjectDrawer onClose={() => setCardOpen(false)} row={null} slug={slug} />}
    </div>
  )
}

export function ProjectRail() {
  const b = useBoard()
  const slug = useActiveProjectSlug()
  const collapsed = useValue($railCollapsed)

  if (!slug) {
    return null
  }

  if (collapsed) {
    // A small reopen chip anchored to the chat surface's top-right — the
    // project stays one click away without a standing panel.
    return (
      <Tip label={b.railShow}>
        <button
          className={cn(
            'absolute right-3 top-3 z-20 inline-flex items-center gap-1.5 rounded-full border border-(--ui-stroke-tertiary)',
            'bg-(--ui-bg-elevated) px-2 py-1 text-[0.6875rem] text-(--ui-text-secondary) shadow-sm',
            'transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'
          )}
          onClick={() => $railCollapsed.set(false)}
          type="button"
        >
          <Codicon name="project" size="0.75rem" />
          {slug}
        </button>
      </Tip>
    )
  }

  return (
    <aside className="h-full w-[19rem] shrink-0 border-l border-(--ui-stroke-tertiary) bg-(--ui-bg-chrome)">
      <RailBody slug={slug} />
    </aside>
  )
}
