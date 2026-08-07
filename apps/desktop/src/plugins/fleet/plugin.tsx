/**
 * Fleet — sandboxed.sh projects as a live background-agents surface. A first-
 * class `/fleet` page + a sidebar nav row + a live statusbar count of running
 * agents, all reusing the plugin's own `/api/plugins/fleet` router through
 * `ctx.rest`. No core edits.
 *
 * Ships OFF by default (`defaultEnabled: false`): it registers nothing until the
 * user turns it on in Settings ▸ Plugins.
 */

import {
  cn,
  type HermesPlugin,
  host,
  icons,
  PALETTE_AREA,
  type PaletteContribution,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { atom } from 'nanostores'

import { bindApi, fetchProjects, liveMissions, needsAttention, projectsKey } from './api'
import { FLEET_LOCALES } from './i18n'
import { FleetPage } from './page'

const t = FLEET_LOCALES.en

/** Bound once at register() so the statusbar and page share one query cache. */
const $bound = atom(false)

// Live "N agents" pill — one glance at fleet activity from anywhere, clicks
// through to the page. Shares the projects query (one cache, one poll). Hidden
// when nothing is in flight or the surface is unbound/unavailable.
function FleetCount() {
  const bound = useValue($bound)

  const { data } = useQuery({
    queryFn: fetchProjects,
    queryKey: projectsKey,
    enabled: bound,
    refetchInterval: 60_000
  })

  if (!data) {return null}
  const projects = data.projects.filter(p => p.bucket !== 'archived')
  const active = projects.reduce((n, p) => n + liveMissions(p).length, 0)
  const attention = projects.reduce((n, p) => n + needsAttention(p).length, 0)

  if (active === 0 && attention === 0) {return null}
  const FleetIcon = icons.Activity

  return (
    <button
      className={cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
        attention > 0 && 'text-amber-400/80'
      )}
      onClick={() => host.navigate('/fleet')}
      title={t.countTip(active, attention)}
      type="button"
    >
      <FleetIcon className="h-[0.7rem] w-[0.7rem]" />
      <span>{active}</span>
      {attention > 0 && <span className="text-amber-400/90">!{attention}</span>}
    </button>
  )
}

const plugin: HermesPlugin = {
  id: 'fleet',
  name: 'Fleet',
  defaultEnabled: false,
  register(ctx) {
    ctx.i18n.register(FLEET_LOCALES)
    bindApi(ctx.rest, ctx.socket)
    $bound.set(true)

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/fleet' } satisfies RouteContribution,
        render: () => <FleetPage />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'rocket', label: t.title, path: '/fleet' } satisfies SidebarNavContribution
      },
      {
        id: 'count',
        area: STATUSBAR_AREAS.right,
        order: 82,
        render: () => <FleetCount />
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'fleet.open',
          label: t.open,
          keywords: ['fleet', 'projects', 'missions', 'agents', 'sandboxed'],
          run: () => host.navigate('/fleet')
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin
