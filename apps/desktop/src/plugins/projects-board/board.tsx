/**
 * The Projects page — mounted at `/projects` (legacy `/board` redirects) in the workspace pane. A
 * kanban-style board of sandboxed.sh projects: four bucket columns (attention /
 * active / paused / archived), project cards with live mission chips and a
 * conic-arc border while agents are working, drag between the lifecycle
 * columns (optimistic, mapped onto pause/resume/archive/unarchive), and a
 * detail drawer on click.
 */

import {
  $sessionColorById,
  $sessionUnreadCounts,
  Button,
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
  ErrorState,
  host,
  Loader,
  relativeTime,
  sessionColorForId,
  SessionStatusDot,
  Tip,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { type CSSProperties, type DragEvent as ReactDragEvent, useEffect, useRef, useState } from 'react'

import {
  $collapsedColumns,
  $focusAttention,
  $introDismissed,
  $notifyAttention,
  $openProjectSlug,
  BOARD_BUCKETS,
  bucketAction,
  fetchProjects,
  fetchProjectTasks,
  leadSignal,
  isBoundConversation,
  liveMissions,
  moveProject,
  needsAttention,
  type ProjectAction,
  projectAction,
  projectMode,
  type ProjectRow,
  PROJECTS_KEY,
  type ProjectsResponse,
  tasksKey
} from './api'
import { RenameProjectDialog, SessionColorSwatchesRow, UnreadBadge } from './color-swatches'
import { ControllerStatusIcon } from './controller-status'
import { AutonomyChip, ProjectDrawer } from './drawer'
import { type BoardText, bucketHelp, bucketLabel, useBoard } from './i18n'

// Column dot/accent tones, all UI tokens or the shared amber/indigo accents.
const BUCKET_TONE: Record<string, string> = {
  active: '#818cf8',
  archived: 'var(--ui-text-quaternary)',
  attention: '#fbbf24',
  paused: 'var(--ui-text-tertiary)'
}

export const bucketTone = (name: string) => BUCKET_TONE[name] ?? 'var(--ui-stroke-primary)'

// The electron REST bridge throws `Error("409: {\"detail\":\"…\"}")`; pull out
// the human-readable detail for a toast.
export function errText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const brace = raw.indexOf('{')

  if (brace !== -1) {
    try {
      return (JSON.parse(raw.slice(brace)) as { detail?: string }).detail ?? raw
    } catch {
      // Not JSON — fall through to the raw message.
    }
  }

  return raw
}

/** Backend timestamps are ISO strings; the canonical formatter takes ms. */
export const ago = (iso?: null | string): null | string => {
  if (!iso) {
    return null
  }

  const ms = Date.parse(iso)

  return Number.isNaN(ms) ? null : relativeTime(ms)
}

// ── card pieces ──────────────────────────────────────────────────────────────

export function ModeChip({ project }: { project: ProjectRow }) {
  const mode = projectMode(project)

  if (!mode) {
    return null
  }

  const label = mode.cause ? `${mode.base}: ${mode.cause}` : mode.base
  const tone = mode.base === 'blocked' ? BUCKET_TONE.attention : mode.base === 'active' ? BUCKET_TONE.active : 'var(--ui-text-quaternary)'

  return (
    <span
      className="inline-flex min-w-0 shrink items-center gap-1 truncate text-[0.625rem] uppercase tracking-wide"
      style={{ color: tone }}
      title={label}
    >
      <span className="size-1 shrink-0 rounded-full" style={{ backgroundColor: tone }} />
      <span className="truncate">{label}</span>
    </span>
  )
}

/** One dense "2 live · 4 failed · 35 missions" line — zero terms omitted. */
function HealthDigest({ b, project }: { b: BoardText; project: ProjectRow }) {
  const health = project.health

  if (!health || health.missions === 0) {
    return null
  }

  const live = liveMissions(project).length

  const terms = [
    live > 0 && <span key="live">{b.live(live)}</span>,
    health.failed > 0 && (
      <span className="text-destructive" key="failed">
        {b.failed(health.failed)}
      </span>
    ),
    health.overdue > 0 && (
      <span className="text-amber-500" key="overdue">
        {b.overdue(health.overdue)}
      </span>
    ),
    <span key="total">{b.missions(health.missions)}</span>
  ].filter(Boolean)

  return (
    <div className="flex flex-wrap items-center gap-y-0.5 text-[0.625rem] text-(--ui-text-tertiary)">
      {terms.map((term, index) => (
        <span className="inline-flex items-center" key={index}>
          {index > 0 && <span className="mx-1 text-(--ui-text-quaternary)">·</span>}
          {term}
        </span>
      ))}
    </div>
  )
}

// ── card ─────────────────────────────────────────────────────────────────────

function Card({
  onMove,
  onOpen,
  project
}: {
  onMove: (slug: string, toBucket: string) => void
  onOpen: (slug: string) => void
  project: ProjectRow
}) {
  const b = useBoard()
  const queryClient = useQueryClient()
  const [dragging, setDragging] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const unreadCounts = useValue($sessionUnreadCounts)
  // Subscribe to the shared color map so the border repaints with the session.
  useValue($sessionColorById)
  const attention = project.bucket === 'attention'
  const live = liveMissions(project)
  const waiting = needsAttention(project)
  const tone = attention ? BUCKET_TONE.attention : bucketTone(project.bucket)
  // The bound conversation's resolved color owns the left edge — instant
  // project ↔ session matching against the sidebar; bucket tone is the
  // fallback for unbound/colorless projects.
  const boundConversation = isBoundConversation(project.conversation) ? project.conversation : null
  const sessionTone = boundConversation ? sessionColorForId(boundConversation.session_id) : undefined
  const update = project.latest_update
  const updateAgo = ago(update?.at)
  // Freshest proof of life for the card's time chip: a SILENT heartbeat (the
  // controller ran but had nothing to say) still counts as "checked N ago", so
  // a quiet-but-alive controller does not look frozen. A subtle dot marks a
  // silent check apart from a delivered update.
  const heartbeatMs = project.controller_heartbeat_at ? Date.parse(project.controller_heartbeat_at) : Number.NaN
  const updateMs = update?.at ? Date.parse(update.at) : Number.NaN
  const silentCheck = !Number.isNaN(heartbeatMs) && (Number.isNaN(updateMs) || heartbeatMs > updateMs)
  const activityAgo = silentCheck ? ago(project.controller_heartbeat_at) : updateAgo
  const draggable = project.bucket !== 'attention'
  const lead = leadSignal(project)

  // Roadmap progress at a glance. Shares the drawer's query key, so opening a
  // card costs nothing extra; long staleTime keeps the board from re-fetching
  // every project's tasks on each poll.
  const { data: roadmap } = useQuery({
    queryFn: () => fetchProjectTasks(project.slug),
    queryKey: tasksKey(project.slug),
    retry: false,
    staleTime: 120_000
  })

  const roadmapSummary = roadmap?.summary && roadmap.summary.total > 0 ? roadmap.summary : null

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            'group relative flex flex-col gap-1 rounded-md border border-(--ui-stroke-tertiary) border-l-2 bg-(--ui-bg-elevated) p-2',
            'transition-colors hover:bg-primary/[0.06]',
            draggable && 'cursor-grab active:cursor-grabbing',
            dragging && 'opacity-40'
          )}
          draggable={draggable}
          onClick={() => onOpen(project.slug)}
          onDragEnd={() => setDragging(false)}
          onDragStart={event => {
            event.dataTransfer.setData('text/plain', project.slug)
            event.dataTransfer.effectAllowed = 'move'
            // Snapshot the drag image before dimming the source, so the ghost
            // stays a solid card.
            event.dataTransfer.setDragImage(event.currentTarget, event.nativeEvent.offsetX, event.nativeEvent.offsetY)
            setDragging(true)
          }}
          style={
            {
              '--mb-tone': attention ? BUCKET_TONE.attention : BUCKET_TONE.active,
              borderLeftColor: sessionTone ?? tone
            } as CSSProperties
          }
        >
          {/* Machine-activity arc: animates only while a mission is live. */}
          {live.length > 0 && !dragging && <span aria-hidden className="mb-arc" />}
          <div className="flex min-w-0 items-center gap-1.5">
            {/* The bound conversation's dot — the exact color/status primitive
                the sidebar renders, so project ↔ session link visually. */}
            {boundConversation && (
              <SessionStatusDot storedSessionId={boundConversation.session_id} />
            )}
            <span className="min-w-0 flex-1 truncate text-[0.75rem] font-medium leading-snug text-foreground">
              {project.title?.trim() || project.slug}
            </span>
            <ControllerStatusIcon project={project} />
            {boundConversation && (
              <UnreadBadge count={unreadCounts[boundConversation.session_id] ?? 0} />
            )}
          </div>
          {/* Mode + last-activity time share one row — no per-line sprawl.
              The time is the freshest proof of life; a subtle dot marks a
              silent controller check (alive, nothing new) apart from a
              delivered update. */}
          {(projectMode(project) || activityAgo || roadmapSummary) && (
            <div className="flex items-center gap-2">
              <ModeChip project={project} />
              {roadmapSummary && (
                <span
                  className="inline-flex shrink-0 items-center gap-1 text-[0.5625rem] tabular-nums text-(--ui-text-quaternary)"
                  title={b.roadmapProgress(roadmapSummary.done, roadmapSummary.total)}
                >
                  <Codicon name="checklist" size="0.65rem" />
                  {roadmapSummary.done}/{roadmapSummary.total}
                </span>
              )}
              {activityAgo && (
                <span
                  className="ml-auto inline-flex shrink-0 items-center gap-1 text-[0.5625rem] text-(--ui-text-quaternary)"
                  title={silentCheck ? b.controllerCheckedTip(activityAgo) : undefined}
                >
                  {silentCheck && <span aria-hidden className="h-1 w-1 shrink-0 rounded-full bg-current opacity-50" />}
                  {activityAgo}
                </span>
              )}
            </div>
          )}
          {attention && project.attention_reasons.length > 0 && (
            <div className="line-clamp-2 text-[0.625rem] leading-snug text-amber-500">
              {project.attention_reasons[0]}
            </div>
          )}
          {lead.controllerBehind && (
            <div className="text-[0.5625rem] uppercase tracking-wide text-amber-500">{b.controllerBehind}</div>
          )}
          {lead.nextAction && (
            <div className="line-clamp-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)">
              {b.nextActionArrow(lead.nextAction)}
            </div>
          )}
          {lead.headline && lead.headline !== lead.nextAction && (
            <div className="line-clamp-2 text-[0.625rem] leading-snug text-(--ui-text-tertiary)">{lead.headline}</div>
          )}
          <HealthDigest b={b} project={project} />
          {/* Attention accents: owner decisions + parked missions. The full
              mission list lives in the drawer, not on the card. */}
          {((project.pending_decisions ?? 0) > 0 || waiting.length > 0 || project.autonomy_level) && (
            <div className="flex flex-wrap items-center gap-2">
              {(project.pending_decisions ?? 0) > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/15 px-1.5 py-px text-[0.5625rem] uppercase tracking-wide text-amber-500">
                  <Codicon name="question" size="0.65rem" />
                  {b.decisionsBadge(project.pending_decisions!)}
                </span>
              )}
              {waiting.length > 0 && (
                <span className="text-[0.5625rem] uppercase tracking-wide text-amber-500">{b.needsYou(waiting.length)}</span>
              )}
              <span className="ml-auto">
                <AutonomyChip level={project.autonomy_level} />
              </span>
            </div>
          )}
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>
        <ContextMenuItem onSelect={() => onOpen(project.slug)}>
          <Codicon name="link-external" size="0.85rem" />
          {b.openProject}
        </ContextMenuItem>
        {boundConversation && (
          <ContextMenuSub>
            <ContextMenuSubTrigger>
              <Codicon name="symbol-color" size="0.85rem" />
              {b.setColor}
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="p-2">
              <SessionColorSwatchesRow sessionId={boundConversation.session_id} />
            </ContextMenuSubContent>
          </ContextMenuSub>
        )}
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={() => setRenameOpen(true)}>
          <Codicon name="edit" size="0.85rem" />
          {b.renameProject}
        </ContextMenuItem>
        {BOARD_BUCKETS.filter(name => bucketAction(project.bucket, name) !== null).map(name => (
          <ContextMenuItem key={name} onSelect={() => onMove(project.slug, name)}>
            <span className="size-2 rounded-full" style={{ backgroundColor: bucketTone(name) }} />
            {b.moveTo(bucketLabel(b, name))}
          </ContextMenuItem>
        ))}
        <ContextMenuSeparator />
        <ContextMenuItem
          className="text-(--ui-text-danger) data-highlighted:text-(--ui-text-danger)"
          onSelect={() => setConfirmDeleteOpen(true)}
        >
          <Codicon name="trash" size="0.85rem" />
          {b.deleteProject}
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
    <RenameProjectDialog onClose={() => setRenameOpen(false)} open={renameOpen} project={project} />
    <ConfirmDialog
      confirmLabel={b.deleteConfirm}
      description={b.deleteConfirmBody}
      destructive
      dismissOnConfirm
      onClose={() => setConfirmDeleteOpen(false)}
      onConfirm={async () => {
        await projectAction(project.slug, 'delete')
        await queryClient.invalidateQueries({ queryKey: PROJECTS_KEY })
      }}
      open={confirmDeleteOpen}
      title={b.deleteConfirmTitle(project.title?.trim() || project.slug)}
    />
    </>
  )
}

// ── column ───────────────────────────────────────────────────────────────────

function Column({
  collapsed,
  highlight = false,
  name,
  onDropProject,
  onMove,
  onOpen,
  onToggle,
  projects
}: {
  collapsed: boolean
  highlight?: boolean
  name: string
  onDropProject: (slug: string, toBucket: string) => void
  onMove: (slug: string, toBucket: string) => void
  onOpen: (slug: string) => void
  onToggle: () => void
  projects: ProjectRow[]
}) {
  const b = useBoard()
  const [over, setOver] = useState(false)
  const tone = bucketTone(name)
  const label = bucketLabel(b, name)
  // Attention is computed from health, never assigned — drops are refused the
  // same way kanban's locked lanes refuse them (no preventDefault → no-drop
  // cursor, drop never fires).
  const locked = name === 'attention'

  const dragHandlers = {
    onDragLeave: () => setOver(false),
    onDragOver: (event: ReactDragEvent<HTMLElement>) => {
      if (locked) {
        event.dataTransfer.dropEffect = 'none'

        return
      }

      event.preventDefault()
      event.dataTransfer.dropEffect = 'move'
      setOver(true)
    },
    onDrop: (event: ReactDragEvent<HTMLElement>) => {
      event.preventDefault()
      setOver(false)
      const slug = event.dataTransfer.getData('text/plain')

      if (slug) {
        onDropProject(slug, name)
      }
    }
  }

  const wash = over && !locked ? 'bg-(--ui-bg-quinary)' : 'bg-[color-mix(in_srgb,var(--ui-bg-quinary)_50%,transparent)]'

  // Collapsed = a thin vertical rail: dot, sideways label, count. Still a live
  // drop target; click expands.
  if (collapsed) {
    return (
      <button
        {...dragHandlers}
        aria-label={b.expand(label)}
        className={cn(
          'flex h-full w-8 shrink-0 flex-col items-center gap-1.5 rounded-lg p-2 transition-colors hover:bg-(--ui-bg-quinary)',
          wash
        )}
        onClick={onToggle}
        type="button"
      >
        <span className="grid h-5 shrink-0 place-items-center">
          <span className="size-1.5 rounded-full" style={{ backgroundColor: tone }} />
        </span>
        <span className="text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary) [writing-mode:vertical-rl]">
          {label}
        </span>
        {projects.length > 0 && (
          <span className="text-[0.625rem] tabular-nums text-(--ui-text-quaternary)">{projects.length}</span>
        )}
      </button>
    )
  }

  return (
    <div
      {...dragHandlers}
      className={cn(
        'group/col flex h-full w-64 shrink-0 flex-col rounded-lg p-2 transition-[background-color,box-shadow]',
        wash,
        highlight && 'ring-1 ring-amber-400/60'
      )}
    >
      <header className="mb-1.5 flex h-5 items-center gap-1.5 px-1">
        <span className="size-1.5 rounded-full" style={{ backgroundColor: tone }} />
        <Tip label={bucketHelp(b, name)}>
          <span className="cursor-help text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)">
            {label}
          </span>
        </Tip>
        <span className="text-[0.625rem] tabular-nums text-(--ui-text-quaternary)">{projects.length}</span>
        <button
          aria-label={b.collapse(label)}
          className="ml-auto grid size-5 place-items-center rounded text-(--ui-text-tertiary) opacity-0 transition-opacity hover:bg-(--chrome-action-hover) hover:text-foreground focus-visible:opacity-100 group-hover/col:opacity-100"
          onClick={onToggle}
          type="button"
        >
          <Codicon name="chevron-left" size="0.75rem" />
        </button>
      </header>
      <div className="mb-column-scroll relative flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
        {projects.map(project => (
          <Card key={project.slug} onMove={onMove} onOpen={onOpen} project={project} />
        ))}
        {projects.length === 0 && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center text-[0.6875rem] text-(--ui-text-quaternary)">
            {b.colEmpty}
          </div>
        )}
      </div>
    </div>
  )
}

// ── intro ────────────────────────────────────────────────────────────────────

function Intro() {
  const b = useBoard()
  const dismissed = useValue($introDismissed)

  if (dismissed) {
    return null
  }

  return (
    <div
      className="mx-4 mb-2 flex flex-col items-start gap-1.5 rounded-lg bg-(--ui-bg-quinary) px-3 py-2.5 text-[0.75rem] leading-relaxed text-(--ui-text-secondary)"
      data-selectable-text="true"
    >
      <p className="min-w-0">{b.introBody}</p>
      <Button onClick={() => $introDismissed.set(true)} size="inline" variant="textStrong">
        {b.introGotIt}
      </Button>
    </div>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export function ProjectsBoardPage() {
  const b = useBoard()
  const qc = useQueryClient()

  // Live updates ride the events socket (bindApi); this interval is the
  // fallback heartbeat for socketless paths (OAuth remotes, dropped sockets).
  const { data, error } = useQuery({
    queryFn: fetchProjects,
    queryKey: PROJECTS_KEY,
    refetchInterval: 30_000
  })

  const [openSlug, setOpenSlug] = useState<null | string>(null)

  // A drawer-open request raised from outside the page (the sidebar row's
  // "Open board card"): the caller navigates here and parks the slug; the page
  // consumes it on arrival and clears it so a remount can't reopen the drawer.
  const requestedSlug = useValue($openProjectSlug)

  useEffect(() => {
    if (requestedSlug === null) {
      return
    }

    setOpenSlug(requestedSlug)
    $openProjectSlug.set(null)
  }, [requestedSlug])

  // Focus request for the Needs-attention column (statusbar !N, notification
  // click-through): expand it, scroll it into view, flash a highlight.
  const focusRequested = useValue($focusAttention)
  const [attentionFlash, setAttentionFlash] = useState(false)
  const columnsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!focusRequested) {
      return
    }

    $focusAttention.set(false)
    const overrides = { ...$collapsedColumns.get() }
    delete overrides.attention
    overrides.attention = false
    $collapsedColumns.set(overrides)
    // Attention is the first column — scroll the strip home.
    columnsRef.current?.scrollTo({ behavior: 'smooth', left: 0 })
    setAttentionFlash(true)
    const timer = window.setTimeout(() => setAttentionFlash(false), 2_400)

    return () => window.clearTimeout(timer)
  }, [focusRequested])

  const notifyOn = useValue($notifyAttention)

  const actionMut = useMutation({
    mutationFn: ({ action, slug }: { action: ProjectAction; slug: string; toBucket: string }) =>
      projectAction(slug, action),
    onMutate: async ({ slug, toBucket }: { action: ProjectAction; slug: string; toBucket: string }) => {
      await qc.cancelQueries({ queryKey: PROJECTS_KEY })
      const previous = qc.getQueryData<ProjectsResponse>(PROJECTS_KEY)

      if (previous) {
        qc.setQueryData(PROJECTS_KEY, moveProject(previous, slug, toBucket))
      }

      return { previous }
    },
    onError: (err, _vars, context) => {
      if (context?.previous) {
        qc.setQueryData(PROJECTS_KEY, context.previous)
      }

      host.notify({ kind: 'error', message: errText(err) })
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: PROJECTS_KEY })
  })

  const onMove = (slug: string, toBucket: string) => {
    const project = data?.projects.find(candidate => candidate.slug === slug)

    if (!project) {
      return
    }

    const action = bucketAction(project.bucket, toBucket)

    if (!action) {
      if (toBucket === 'attention' || project.bucket === 'attention') {
        host.notify({ kind: 'info', message: b.attentionLocked })
      }

      return
    }

    actionMut.mutate({ action, slug, toBucket })
  }

  // A 503 is "no sandboxed.sh here" — hide the surface rather than alarm.
  const status = (error as { status?: number } | null)?.status
  const errorMessage = error ? (status === 503 ? b.empty : b.unreachable) : null

  const overrides = useValue($collapsedColumns)

  const columns = BOARD_BUCKETS.map(name => ({
    name,
    projects: data?.projects.filter(project => project.bucket === name) ?? []
  }))

  const hasProjects = (data?.projects.length ?? 0) > 0
  const totalLive = data?.projects.reduce((n, p) => (p.bucket === 'archived' ? n : n + liveMissions(p).length), 0) ?? 0

  const toggleColumn = (name: string, auto: boolean) => {
    const next = { ...overrides }
    const value = !(next[name] ?? auto)

    if (value === auto) {
      delete next[name]
    } else {
      next[name] = value
    }

    $collapsedColumns.set(next)
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-(--ui-surface-background)">
      <header className="flex shrink-0 items-center gap-2 px-4 py-2">
        <h1 className="text-sm font-semibold text-foreground">{b.title}</h1>
        <span className="rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] tabular-nums text-(--ui-text-tertiary)">
          {data?.projects.length ?? 0}
        </span>
        {totalLive > 0 && <span className="text-[0.6875rem] text-(--ui-text-tertiary)">{b.liveCount(totalLive)}</span>}
        <div className="ml-auto">
          <Tip label={notifyOn ? b.notifyToggleOn : b.notifyToggleOff}>
            <Button
              aria-label={notifyOn ? b.notifyToggleOn : b.notifyToggleOff}
              className={cn(!notifyOn && 'opacity-50')}
              onClick={() => $notifyAttention.set(!notifyOn)}
              size="icon-xs"
              variant="ghost"
            >
              <Codicon name={notifyOn ? 'bell' : 'bell-slash'} size="0.85rem" />
            </Button>
          </Tip>
        </div>
      </header>

      {data && <Intro />}

      {errorMessage && !data ? (
        <div className="grid flex-1 place-items-center">
          <ErrorState title={errorMessage} />
        </div>
      ) : !data ? (
        <div className="grid flex-1 place-items-center">
          <Loader type="lemniscate-bloom" />
        </div>
      ) : !hasProjects ? (
        <div className="grid flex-1 place-items-center px-4 text-center">
          <div className="flex flex-col items-center gap-2">
            <Codicon className="text-(--ui-text-quaternary)" name="project" size="1.25rem" />
            <p className="text-xs text-(--ui-text-tertiary)">{b.noProjects}</p>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 gap-2 overflow-x-auto px-4 pt-1 pb-3" ref={columnsRef}>
          {columns.map(column => {
            const auto = hasProjects && column.projects.length === 0

            return (
              <Column
                collapsed={overrides[column.name] ?? auto}
                highlight={attentionFlash && column.name === 'attention'}
                key={column.name}
                name={column.name}
                onDropProject={onMove}
                onMove={onMove}
                onOpen={setOpenSlug}
                onToggle={() => toggleColumn(column.name, auto)}
                projects={column.projects}
              />
            )
          })}
        </div>
      )}

      <ProjectDrawer
        onClose={() => setOpenSlug(null)}
        row={data?.projects.find(project => project.slug === openSlug) ?? null}
        slug={openSlug}
      />
    </div>
  )
}
