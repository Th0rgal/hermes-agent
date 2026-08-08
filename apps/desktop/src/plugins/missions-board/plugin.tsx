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
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  Tip,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { atom } from 'nanostores'

import { bindApi, fetchProjects, liveMissions, needsAttention, PROJECTS_KEY } from './api'
import { MissionsBoardPage } from './board'
import { BOARD_LOCALES, useBoard } from './i18n'

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
    <Tip label={b.countTip(active, attention)}>
      <button
        className={cn(
          'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
          attention > 0 && 'text-amber-500'
        )}
        onClick={() => host.navigate('/board')}
        type="button"
      >
        <Codicon name="project" size="0.7rem" />
        <span>{active}</span>
        {attention > 0 && <span>!{attention}</span>}
      </button>
    </Tip>
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
