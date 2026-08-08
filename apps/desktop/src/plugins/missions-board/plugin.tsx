/**
 * Missions Board — sandboxed.sh projects as a kanban board. A first-class
 * `/board` page + a sidebar nav row + a live statusbar count of running
 * agents, all reusing the plugin's own `/api/plugins/missions-board` router
 * through `ctx.rest`. No core edits.
 *
 * Ships OFF by default (`defaultEnabled: false`): it registers nothing until
 * the user turns it on in Settings ▸ Plugins.
 */

import './missions-board.css'

import {
  cn,
  Codicon,
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  queryClient,
  type RouteContribution,
  ROUTES_AREA,
  SESSIONS_SECTIONS_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  Tip,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { atom } from 'nanostores'

import {
  $focusAttention,
  $openProjectSlug,
  bindApi,
  fetchProjects,
  liveMissions,
  needsAttention,
  type ProjectPaletteRow,
  projectPaletteRows,
  PROJECTS_KEY,
  type ProjectsResponse,
  setAttentionNotifier
} from './api'
import { MissionsBoardPage } from './board'
import { BOARD_LOCALES, useBoard } from './i18n'
import { ProjectsSidebarSection } from './sidebar-section'

/** Bound once at register() so the statusbar and page share one query cache. */
const $bound = atom(false)

// Live "N agents" pill — one glance at board activity from anywhere, clicks
// through to the page. Shares the projects query (one cache, one poll). Hidden
// when nothing is in flight or the surface is unbound/unavailable.
function BoardCount() {
  const b = useBoard()
  const bound = useValue($bound)

  const { data } = useQuery({
    enabled: bound,
    queryFn: fetchProjects,
    queryKey: PROJECTS_KEY,
    refetchInterval: 60_000
  })

  if (!data) {
    return null
  }

  const projects = data.projects.filter(p => p.bucket !== 'archived')
  const active = projects.reduce((n, p) => n + liveMissions(p).length, 0)
  const attention = projects.reduce((n, p) => n + needsAttention(p).length, 0)

  if (active === 0 && attention === 0) {
    return null
  }

  return (
    <span className="inline-flex h-full items-center">
      <Tip label={b.countTip(active, attention)}>
        <button
          className={cn(
            'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
            'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
          )}
          onClick={() => host.navigate('/board')}
          type="button"
        >
          <Codicon name="project" size="0.7rem" />
          <span>{active}</span>
        </button>
      </Tip>
      {attention > 0 && (
        <Tip label={b.focusAttentionTip}>
          <button
            className={cn(
              'inline-flex h-full items-center px-1 text-[0.6875rem] font-medium tabular-nums text-amber-500',
              'transition-colors hover:bg-(--chrome-action-hover)'
            )}
            onClick={() => {
              $focusAttention.set(true)
              host.navigate('/board')
            }}
            type="button"
          >
            !{attention}
          </button>
        </Tip>
      )}
    </span>
  )
}

const plugin: HermesPlugin = {
  id: 'missions-board',
  name: 'Missions Board',
  defaultEnabled: false,
  register(ctx) {
    ctx.i18n.register(BOARD_LOCALES)
    ctx.onDispose(bindApi(ctx.rest, ctx.storage, ctx.socket))
    $bound.set(true)
    ctx.onDispose(() => $bound.set(false))

    // Desktop notification when a project ENTERS attention (the data layer
    // detects transitions + debounces; this owns the i18n'd presentation).
    // Clicking through opens the board focused on the attention column.
    setAttentionNotifier(slug =>
      host.notify({
        action: {
          label: ctx.i18n.t('openBoardAction'),
          onClick: () => {
            $focusAttention.set(true)
            host.navigate('/board')
          }
        },
        kind: 'warning',
        message: ctx.i18n.t('attentionNotifBody', slug),
        title: ctx.i18n.t('attentionNotifTitle')
      })
    )
    ctx.onDispose(() => setAttentionNotifier(null))

    // ⌘K rows per project, regenerated whenever the roster query lands with a
    // different shape (slug/binding signature) — registerMany's disposer drops
    // the previous batch, so stale projects fall out of the palette.
    let disposePalette: (() => void) | null = null
    let paletteSignature = ''

    const paletteRun = (row: ProjectPaletteRow) => () => {
      if (row.kind === 'chat' && row.sessionId) {
        host.navigate(`/${encodeURIComponent(row.sessionId)}`)
      } else {
        $openProjectSlug.set(row.slug)
        host.navigate('/board')
      }
    }

    const syncPalette = () => {
      const data = queryClient.getQueryData<ProjectsResponse>(PROJECTS_KEY)

      if (!data) {
        return
      }

      const rows = projectPaletteRows(data.projects)
      const signature = rows.map(row => `${row.kind}:${row.slug}:${row.sessionId ?? ''}`).join('|')

      if (signature === paletteSignature) {
        return
      }

      paletteSignature = signature
      disposePalette?.()
      disposePalette = ctx.registerMany(
        rows.map(row => ({
          id: `palette-${row.kind}-${row.slug}`,
          area: PALETTE_AREA,
          data: {
            id: `missions-board.${row.kind}.${row.slug}`,
            label:
              row.kind === 'chat'
                ? ctx.i18n.t('paletteOpenConversation', row.slug)
                : ctx.i18n.t('paletteOpenCard', row.slug),
            keywords: ['project', row.slug, 'missions', 'board'],
            run: paletteRun(row)
          } satisfies PaletteContribution
        }))
      )
    }

    ctx.onDispose(queryClient.getQueryCache().subscribe(syncPalette))
    ctx.onDispose(() => disposePalette?.())
    syncPalette()

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/board' } satisfies RouteContribution,
        render: () => <MissionsBoardPage />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'project', label: ctx.i18n.t('nav'), path: '/board' } satisfies SidebarNavContribution
      },
      {
        // Bound project conversations above the sidebar's Pinned section;
        // renders null (whole section hidden) on 503/empty, and unregisters
        // with the plugin.
        id: 'sidebar-section',
        area: SESSIONS_SECTIONS_AREA,
        render: () => <ProjectsSidebarSection />
      },
      {
        id: 'count',
        area: STATUSBAR_AREAS.right,
        order: 82,
        render: () => <BoardCount />
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'missions-board.open',
          label: ctx.i18n.t('open'),
          keywords: ['missions', 'board', 'projects', 'agents', 'sandboxed'],
          run: () => host.navigate('/board')
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin
