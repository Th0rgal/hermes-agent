/**
 * "Projects" chat-sidebar section — contributed into SESSIONS_SECTIONS_AREA
 * (rendered above Pinned). One row per non-archived sandboxed.sh project with
 * a bound control conversation: the session's own status/color dot (the same
 * SessionStatusDot primitive — same resolved color as the Pinned row), the
 * project slug, an unread-count pill (messages since the session was last
 * open, capped at 9+, cleared by opening the session — the same signal that
 * clears the core unread dot), and a controller-status icon when the
 * controller is stopped (paused by the operator, self-stopped, or gone
 * silent). Click = open the session. A hover ⋯ menu (and right-click) offers
 * Set color / Open board card / Pause–Resume.
 *
 * Hides itself entirely (renders null) when the surface is unavailable (503 /
 * error), still loading, or no project has a binding.
 */

import {
  $focusedStoredSessionId,
  $sessionUnreadCounts,
  cn,
  Codicon,
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  host,
  sessionIdsRefer,
  SessionStatusDot,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import {
  $openProjectSlug,
  fetchProjects,
  type ProjectAction,
  projectAction,
  type ProjectRow,
  PROJECTS_KEY
} from './api'
import { errText } from './board'
import { DeleteProjectDialog, SessionColorSwatchesRow, UnreadBadge } from './color-swatches'
import { ControllerStatusIcon } from './controller-status'
import { useBoard } from './i18n'

type BoundRow = ProjectRow & { conversation: { session_id: string } }

function hasBinding(project: ProjectRow): project is BoundRow {
  return project.bucket !== 'archived' && Boolean(project.conversation?.session_id)
}

/** The row's action set, rendered through either Radix kit (⋯ dropdown and
 *  right-click context menu) so the two present identically. */
function useRowActions(project: BoundRow) {
  const b = useBoard()
  const qc = useQueryClient()
  const paused = project.bucket === 'paused'

  const lifecycle = useMutation({
    mutationFn: (action: ProjectAction) => projectAction(project.slug, action),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSettled: () => void qc.invalidateQueries({ queryKey: PROJECTS_KEY })
  })

  return {
    b,
    lifecycleLabel: paused ? b.resumeProject : b.pauseProject,
    onLifecycle: () => lifecycle.mutate(paused ? 'resume' : 'pause'),
    onOpenCard: () => {
      $openProjectSlug.set(project.slug)
      host.navigate('/projects')
    }
  }
}

function RowMenuItems({
  actions,
  kit,
  onDelete,
  sessionId
}: {
  actions: ReturnType<typeof useRowActions>
  kit: 'context' | 'dropdown'
  onDelete: () => void
  sessionId: string
}) {
  const { b, lifecycleLabel, onLifecycle, onOpenCard } = actions

  const [Item, Separator, Sub, SubTrigger, SubContent] =
    kit === 'context'
      ? ([ContextMenuItem, ContextMenuSeparator, ContextMenuSub, ContextMenuSubTrigger, ContextMenuSubContent] as const)
      : ([
          DropdownMenuItem,
          DropdownMenuSeparator,
          DropdownMenuSub,
          DropdownMenuSubTrigger,
          DropdownMenuSubContent
        ] as const)

  return (
    <>
      <Sub>
        <SubTrigger>
          <Codicon name="symbol-color" size="0.85rem" />
          {b.setColor}
        </SubTrigger>
        <SubContent className="p-2">
          <SessionColorSwatchesRow sessionId={sessionId} />
        </SubContent>
      </Sub>
      <Item onSelect={onOpenCard}>
        <Codicon name="project" size="0.85rem" />
        {b.openCard}
      </Item>
      <Separator />
      <Item onSelect={onLifecycle}>
        <Codicon name="debug-pause" size="0.85rem" />
        {lifecycleLabel}
      </Item>
      <Item className="text-destructive" onSelect={onDelete}>
        <Codicon name="trash" size="0.85rem" />
        {b.deleteProject}
      </Item>
    </>
  )
}

function ProjectRowItem({ project }: { project: BoundRow }) {
  const b = useBoard()
  const unreadCounts = useValue($sessionUnreadCounts)
  const sessionId = project.conversation.session_id
  const unread = unreadCounts[sessionId] ?? 0
  const actions = useRowActions(project)
  // Selected in lockstep with the core sidebar rows: same focused-session
  // source, lineage-aware matching (the binding may hold either side of a
  // stored-id ↔ live-tip pair).
  const focusedId = useValue($focusedStoredSessionId)
  const selected = sessionIdsRefer(focusedId, sessionId)
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>
        <div className="group/mbrow relative">
          <button
            className={cn(
              'flex h-7 w-full items-center gap-2 rounded-md px-2 pr-7 text-left text-[0.8125rem] text-(--ui-text-secondary)',
              'transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground',
              // The exact selected-row treatment core session rows use.
              selected && 'bg-(--ui-row-active-background) text-foreground'
            )}
            onClick={() => host.navigate(`/${encodeURIComponent(sessionId)}`)}
            type="button"
          >
            {/* Same dot the sidebar's own rows render — same resolved color. */}
            <SessionStatusDot storedSessionId={sessionId} />
            <span className="min-w-0 flex-1 truncate">{project.title?.trim() || project.slug}</span>
            <UnreadBadge count={unread} />
            <ControllerStatusIcon project={project} />
          </button>
          {/* Hover ⋯ — overlaid so it never shifts the row's layout. */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                aria-label={b.menuOptions}
                className={cn(
                  'absolute right-1 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded text-(--ui-text-tertiary)',
                  'opacity-0 transition-opacity hover:bg-(--chrome-action-hover) hover:text-foreground',
                  'focus-visible:opacity-100 group-hover/mbrow:opacity-100 data-[state=open]:opacity-100'
                )}
                type="button"
              >
                <Codicon name="ellipsis" size="0.8rem" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <RowMenuItems actions={actions} kit="dropdown" onDelete={() => setConfirmDelete(true)} sessionId={sessionId} />
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>
        <RowMenuItems actions={actions} kit="context" onDelete={() => setConfirmDelete(true)} sessionId={sessionId} />
      </ContextMenuContent>
      </ContextMenu>
      <DeleteProjectDialog onClose={() => setConfirmDelete(false)} open={confirmDelete} project={project} />
    </>
  )
}

export function ProjectsSidebarSection() {
  const b = useBoard()

  // Shares the board's query (one cache, one poll). Errors — including the
  // 503 "no sandboxed.sh here" — just hide the section.
  const { data } = useQuery({
    queryFn: fetchProjects,
    queryKey: PROJECTS_KEY,
    refetchInterval: 60_000,
    retry: false
  })

  const rows = data?.projects.filter(hasBinding) ?? []

  if (rows.length === 0) {
    return null
  }

  return (
    <div className="shrink-0 p-0 pb-1">
      <div className="flex h-6 items-center px-2 text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)">
        {b.sidebarSection}
      </div>
      <div className="flex flex-col gap-px">
        {rows.map(project => (
          <ProjectRowItem key={project.slug} project={project} />
        ))}
      </div>
    </div>
  )
}
