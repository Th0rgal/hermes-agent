/**
 * "Projects" chat-sidebar section — contributed into SESSIONS_SECTIONS_AREA
 * (rendered above Pinned). One row per non-archived sandboxed.sh project with
 * a bound control conversation: the session's own status/color dot (the same
 * SessionStatusDot primitive the sidebar rows render), the project slug, and
 * an amber hint when the project needs attention. Click = open the session.
 *
 * Hides itself entirely (renders null) when the surface is unavailable (503 /
 * error), still loading, or no project has a binding.
 */

import { cn, host, SessionStatusDot, Tip, useQuery } from '@hermes/plugin-sdk'

import { fetchProjects, type ProjectRow, PROJECTS_KEY } from './api'
import { useBoard } from './i18n'

function hasBinding(project: ProjectRow): project is ProjectRow & { conversation: { session_id: string } } {
  return project.bucket !== 'archived' && Boolean(project.conversation?.session_id)
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
          <button
            className={cn(
              'flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[0.8125rem] text-(--ui-text-secondary)',
              'transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground'
            )}
            key={project.slug}
            onClick={() => host.navigate(`/${encodeURIComponent(project.conversation.session_id)}`)}
            type="button"
          >
            {/* Same dot the sidebar's own rows render — same resolved color. */}
            <SessionStatusDot storedSessionId={project.conversation.session_id} />
            <span className="min-w-0 flex-1 truncate">{project.slug}</span>
            {project.bucket === 'attention' && (
              <Tip label={project.attention_reasons[0] ?? b.col.attention.label}>
                <span aria-label={b.col.attention.label} className="size-1.5 shrink-0 rounded-full bg-amber-500" />
              </Tip>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
