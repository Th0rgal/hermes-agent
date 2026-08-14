/**
 * "Projects" chat-sidebar section — contributed into SESSIONS_SECTIONS_AREA
 * (rendered above Pinned). One row per non-archived sandboxed.sh project with
 * a bound control conversation: the session's own status/color dot (the same
 * SessionStatusDot primitive — same resolved color as the Pinned row), the
 * project slug, an unread-count pill (messages since the session was last
 * open, capped at 9+, cleared by opening the session — the same signal that
 * clears the core unread dot), and an amber hint when the project needs
 * attention. Click = open the session. A hover ⋯ menu (and right-click) offers
 * Set color / Open board card / Pause–Resume.
 *
 * Hides itself entirely (renders null) when the surface is unavailable (503 /
 * error), still loading, or no project has a binding.
 */

import {
  $activeChatSessionIds,
  $sessionUnreadCounts,
  cn,
  Codicon,
  ConfirmDialog,
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
  SessionStatusDot,
  Tip,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import {
  fetchProjects,
  isBoundConversation,
  type ProjectAction,
  projectAction,
  type ProjectRow,
  PROJECTS_KEY
} from './api'
import { errText } from './board'
import { RenameProjectDialog, SessionColorSwatchesRow, UnreadBadge } from './color-swatches'
import { ControllerStatusIcon } from './controller-status'
import { ProjectDrawer } from './drawer'
import { useBoard } from './i18n'

type BoundRow = ProjectRow & { conversation: { session_id: string } }

function hasBinding(project: ProjectRow): project is BoundRow {
  return project.bucket !== 'archived' && isBoundConversation(project.conversation)
}

/** The row's action set, rendered through either Radix kit (⋯ dropdown and
 *  right-click context menu) so the two present identically. */
function useRowActions(project: BoundRow, onOpenCard: () => void) {
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
    // The card opens IN PLACE (a dialog over whatever view is active) —
    // navigating away to the board just to peek at the roadmap threw the
    // user out of the conversation they were in.
    onOpenCard
  }
}

function RowMenuItems({
  actions,
  kit,
  onDelete,
  onRename,
  sessionId
}: {
  actions: ReturnType<typeof useRowActions>
  kit: 'context' | 'dropdown'
  onDelete: () => void
  onRename: () => void
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
      <Item onSelect={onRename}>
        <Codicon name="edit" size="0.85rem" />
        {b.renameProject}
      </Item>
      <Separator />
      <Item onSelect={onLifecycle}>
        <Codicon name="debug-pause" size="0.85rem" />
        {lifecycleLabel}
      </Item>
      <Separator />
      <Item
        className="text-(--ui-text-danger) data-highlighted:text-(--ui-text-danger)"
        onSelect={onDelete}
      >
        <Codicon name="trash" size="0.85rem" />
        {b.deleteProject}
      </Item>
    </>
  )
}

function ProjectRowItem({ project }: { project: BoundRow }) {
  const b = useBoard()
  const qc = useQueryClient()
  const unreadCounts = useValue($sessionUnreadCounts)
  const activeChatIds = useValue($activeChatSessionIds)
  const sessionId = project.conversation.session_id
  const unread = unreadCounts[sessionId] ?? 0
  const [cardOpen, setCardOpen] = useState(false)
  const actions = useRowActions(project, () => setCardOpen(true))
  const [renameOpen, setRenameOpen] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  // The bound conversation is the open chat → this row IS the selection. The
  // core Recents list no longer shows the session, so without this the click
  // would appear to select nothing at all.
  const selected = activeChatIds.includes(sessionId)

  const remove = useMutation({
    mutationFn: () => projectAction(project.slug, 'delete'),
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSettled: () => void qc.invalidateQueries({ queryKey: PROJECTS_KEY })
  })

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div className="group/mbrow relative">
          <button
            className={cn(
              'flex h-7 w-full items-center gap-2 rounded-md px-2 pr-7 text-left text-[0.8125rem] text-(--ui-text-secondary)',
              'transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground',
              selected && 'bg-(--ui-row-active-background) text-foreground'
            )}
            onClick={() => host.navigate(`/${encodeURIComponent(sessionId)}`)}
            type="button"
          >
            {/* Same dot the sidebar's own rows render — same resolved color. */}
            <SessionStatusDot storedSessionId={sessionId} />
            <span className="min-w-0 flex-1 truncate">{project.title?.trim() || project.slug}</span>
            <ControllerStatusIcon project={project} />
            <UnreadBadge count={unread} />
            {project.bucket === 'attention' && (
              <Tip label={project.attention_reasons[0] ?? b.col.attention.label}>
                <span aria-label={b.col.attention.label} className="size-1.5 shrink-0 rounded-full bg-amber-500" />
              </Tip>
            )}
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
              <RowMenuItems
                actions={actions}
                kit="dropdown"
                onDelete={() => setConfirmDeleteOpen(true)}
                onRename={() => setRenameOpen(true)}
                sessionId={sessionId}
              />
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>
        <RowMenuItems
          actions={actions}
          kit="context"
          onDelete={() => setConfirmDeleteOpen(true)}
          onRename={() => setRenameOpen(true)}
          sessionId={sessionId}
        />
      </ContextMenuContent>
    </ContextMenu>
    {cardOpen && <ProjectDrawer onClose={() => setCardOpen(false)} row={project} slug={project.slug} />}
    <RenameProjectDialog onClose={() => setRenameOpen(false)} open={renameOpen} project={project} />
    <ConfirmDialog
      confirmLabel={b.deleteConfirm}
      description={b.deleteConfirmBody}
      destructive
      dismissOnConfirm
      onClose={() => setConfirmDeleteOpen(false)}
      onConfirm={async () => {
        await remove.mutateAsync()
      }}
      open={confirmDeleteOpen}
      title={b.deleteConfirmTitle(project.title?.trim() || project.slug)}
    />
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
