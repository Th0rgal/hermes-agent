import { TooltipProvider } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import {
  $attentionNotifiedAt,
  attentionTransitions,
  bindApi,
  bucketAction,
  controllerStop,
  debounceAttentionNotifications,
  type MissionChip,
  moveProject,
  projectPaletteRows,
  type ProjectRow,
  type ProjectsResponse,
  selectChips
} from './api'
import { ProjectsBoardPage } from './board'
import { BOARD_LOCALES } from './i18n'

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(async () => {
  // The host registers plugin bundles via ctx.i18n; the test reaches the same
  // registry directly. Dynamic import: plugin SOURCE files import only the
  // SDK, and the registration door isn't (yet) SDK-exported.
  const { registerPluginLocales } = await import('@/i18n/plugin-i18n')

  registerPluginLocales('projects-board', BOARD_LOCALES)
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ── bucket → lifecycle action mapping ────────────────────────────────────────

describe('bucketAction', () => {
  it('maps the lifecycle drags onto the backend verbs', () => {
    expect(bucketAction('active', 'paused')).toBe('pause')
    expect(bucketAction('paused', 'active')).toBe('resume')
    expect(bucketAction('active', 'archived')).toBe('archive')
    expect(bucketAction('paused', 'archived')).toBe('archive')
    expect(bucketAction('archived', 'active')).toBe('unarchive')
  })

  it('refuses no-op and attention drags', () => {
    expect(bucketAction('active', 'active')).toBeNull()
    expect(bucketAction('attention', 'paused')).toBeNull()
    expect(bucketAction('active', 'attention')).toBeNull()
  })

  it('refuses the ambiguous archived→paused restore', () => {
    // Unarchive restores the backend's own status; only Active is honest.
    expect(bucketAction('archived', 'paused')).toBeNull()
  })
})

// ── optimistic move reducer ──────────────────────────────────────────────────

const row = (slug: string, bucket: string): ProjectRow => ({
  attention_reasons: [],
  bucket,
  latest_update: null,
  missions: [],
  slug
})

describe('moveProject', () => {
  const data: ProjectsResponse = { projects: [row('verity', 'active'), row('hermes', 'paused')] }

  it('re-buckets the named project and nothing else', () => {
    const next = moveProject(data, 'verity', 'paused')

    expect(next.projects.map(p => p.bucket)).toEqual(['paused', 'paused'])
    // Untouched rows keep identity (no spurious re-renders).
    expect(next.projects[1]).toBe(data.projects[1])
  })

  it('is a no-op (same reference) for unknown slugs and same-bucket moves', () => {
    expect(moveProject(data, 'ghost', 'paused')).toBe(data)
    expect(moveProject(data, 'verity', 'active')).toBe(data)
  })
})

// ── compact chip selection ───────────────────────────────────────────────────

const chip = (id: string, status: string, updated_at: null | string = null): MissionChip => ({
  github_pr: null,
  id,
  status,
  title: id,
  updated_at
})

describe('selectChips', () => {
  it('caps at 3 — live first, then most-recent failed — and counts the rest', () => {
    const { chips, overflow } = selectChips([
      chip('f-old', 'failed', '2026-08-01T00:00:00Z'),
      chip('done-1', 'completed'),
      chip('wait', 'awaiting_user'),
      chip('f-new', 'failed', '2026-08-07T00:00:00Z'),
      chip('run', 'active'),
      chip('done-2', 'completed'),
      chip('done-3', 'stopped'),
      chip('done-4', 'interrupted')
    ])

    expect(chips.map(c => c.id)).toEqual(['wait', 'run', 'f-new'])
    expect(overflow).toBe(5)
  })

  it('never renders a wall of dead chips: nothing live → 1 most-recent + N', () => {
    const { chips, overflow } = selectChips([
      chip('f-old', 'failed', '2026-08-01T00:00:00Z'),
      chip('f-new', 'failed', '2026-08-07T00:00:00Z'),
      chip('done', 'completed', '2026-08-03T00:00:00Z')
    ])

    expect(chips.map(c => c.id)).toEqual(['f-new'])
    expect(overflow).toBe(2)
  })

  it('shows all chips (no +N) when they fit under the cap', () => {
    const { chips, overflow } = selectChips([chip('run', 'active')])

    expect(chips.map(c => c.id)).toEqual(['run'])
    expect(overflow).toBe(0)
  })
})

// ── attention transitions + notification debounce ────────────────────────────

describe('attentionTransitions', () => {
  it('reports only projects newly ENTERING attention', () => {
    const previous = { hermes: 'attention', paloma: 'paused', verity: 'active' }

    const entered = attentionTransitions(previous, [
      row('verity', 'attention'), // active → attention: fires
      row('hermes', 'attention'), // already there: silent
      row('paloma', 'paused') // unchanged: silent
    ])

    expect(entered).toEqual(['verity'])
  })

  it('a null previous snapshot (startup) never fires', () => {
    expect(attentionTransitions(null, [row('verity', 'attention')])).toEqual([])
  })

  it('a project first seen already in attention is not a transition', () => {
    expect(attentionTransitions({}, [row('new-proj', 'attention')])).toEqual([])
  })
})

describe('debounceAttentionNotifications', () => {
  it('fires once, then suppresses inside the 30-minute window', () => {
    $attentionNotifiedAt.set({})
    const t0 = 1_000_000_000

    expect(debounceAttentionNotifications(['verity'], t0)).toEqual(['verity'])
    expect(debounceAttentionNotifications(['verity'], t0 + 29 * 60 * 1000)).toEqual([])
    expect(debounceAttentionNotifications(['verity'], t0 + 31 * 60 * 1000)).toEqual(['verity'])

    $attentionNotifiedAt.set({})
  })
})

// ── palette generation ───────────────────────────────────────────────────────

describe('projectPaletteRows', () => {
  it('emits a card row per project and a chat row only when bound, skipping archived', () => {
    const rows = projectPaletteRows([
      { ...row('verity', 'active'), conversation: { session_id: 's1', source: 'binding' } },
      row('paloma', 'paused'),
      { ...row('old', 'archived'), conversation: { session_id: 's2', source: 'binding' } }
    ])

    expect(rows).toEqual([
      { kind: 'chat', label: 'verity', sessionId: 's1', slug: 'verity' },
      { kind: 'card', label: 'verity', slug: 'verity' },
      { kind: 'card', label: 'paloma', slug: 'paloma' }
    ])
  })

  it('caps by project so a huge roster cannot flood the palette', () => {
    const projects = Array.from({ length: 40 }, (_, i) => row(`p${i}`, 'active'))

    expect(projectPaletteRows(projects, 20)).toHaveLength(20)
  })

  it('labels rows by the display title, falling back to slug', () => {
    const rows = projectPaletteRows([
      { ...row('verity-lido', 'active'), title: 'Verity Lido' },
      { ...row('paloma', 'active'), title: null }
    ])

    expect(rows.map(r => r.label)).toEqual(['Verity Lido', 'paloma'])
  })
})

describe('controllerStop staleness', () => {
  const NOW = Date.parse('2026-08-13T12:00:00Z')

  const base = (over: Partial<ProjectRow>): ProjectRow => ({
    ...row('lean-silicon', 'active'),
    controller_cron_id: 'job42',
    mode: 'active',
    ...over
  })

  it('does not read a quiet-but-heartbeating controller as stale when the server says healthy', () => {
    const project = base({
      controller_health: 'healthy',
      // Last delivery 5h ago — [SILENT] ticks since…
      latest_update: { at: '2026-08-13T07:00:00Z', headline: 'x', session_id: 's' } as never,
      // …but the scheduler ran the job 10 minutes ago.
      controller_heartbeat_at: '2026-08-13T11:50:00Z'
    })

    expect(controllerStop(project, NOW)).toBeNull()
  })

  it('a fresh heartbeat alone (no server verdict) also clears the 2h heuristic', () => {
    const project = base({
      latest_update: { at: '2026-08-13T07:00:00Z', headline: 'x', session_id: 's' } as never,
      controller_heartbeat_at: '2026-08-13T11:50:00Z'
    })

    expect(controllerStop(project, NOW)).toBeNull()
  })

  it('still reads stale when both the delivery and the heartbeat are old', () => {
    const project = base({
      latest_update: { at: '2026-08-13T07:00:00Z', headline: 'x', session_id: 's' } as never,
      controller_heartbeat_at: '2026-08-13T08:00:00Z'
    })

    expect(controllerStop(project, NOW)).toEqual({
      kind: 'stale',
      lastAt: Date.parse('2026-08-13T08:00:00Z')
    })
  })

  it('blocked is waiting, not "stopped itself"', () => {
    const project = base({
      mode: 'blocked',
      latest_update: {
        at: '2026-08-15T06:47:15Z',
        headline: 'leanSilicon LSC1-05 — ATTENTE CI EXTERNE',
        session_id: 's'
      } as never
    })

    expect(controllerStop(project, NOW)).toEqual({ cause: null, kind: 'waiting' })
  })

  it('blocked:cause carries the cause on waiting', () => {
    const project = base({ mode: 'blocked:external-ci' })

    expect(controllerStop(project, NOW)).toEqual({ cause: 'external-ci', kind: 'waiting' })
  })

  it('paused is still self-stopped', () => {
    const project = base({ mode: 'paused' })

    expect(controllerStop(project, NOW)).toEqual({ cause: null, kind: 'self-stopped' })
  })

  it('a server stale verdict wins regardless of client timestamps', () => {
    const project = base({
      controller_health: 'stale',
      latest_update: { at: '2026-08-13T11:59:00Z', headline: 'x', session_id: 's' } as never
    })

    expect(controllerStop(project, NOW)?.kind).toBe('stale')
  })
})

// ── board render (mocked rest layer) ─────────────────────────────────────────

describe('the board page', () => {
  function renderBoard(projects: ProjectRow[]) {
    const restCalls: string[] = []

    const rest = <T,>(path: string): Promise<T> => {
      restCalls.push(path)

      if (path === '/projects') {
        return Promise.resolve({ projects } as T)
      }

      return Promise.reject(new Error(`unexpected rest call: ${path}`))
    }

    const dispose = bindApi(rest)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <ProjectsBoardPage />
        </TooltipProvider>
      </QueryClientProvider>
    )

    return { dispose, restCalls }
  }

  it('buckets the roster into the four columns and shows live activity', async () => {
    const { dispose } = renderBoard([
      {
        ...row('verity', 'attention'),
        attention_reasons: ['controller blocked on CI'],
        missions: [{ github_pr: null, id: 'm1', status: 'awaiting_user', title: 'Fix CI', updated_at: null }]
      },
      {
        ...row('hermes', 'active'),
        latest_update: { at: null, headline: 'Shipping PR #42', mode: 'active' },
        // 8 chips in — the card must cap at 3 (live + most-recent failed) + "+5".
        missions: [
          chip('Ship PR', 'active'),
          chip('f1', 'failed', '2026-08-07T00:00:00Z'),
          chip('f2', 'failed', '2026-08-06T00:00:00Z'),
          chip('dead-1', 'stopped'),
          chip('dead-2', 'stopped'),
          chip('dead-3', 'interrupted'),
          chip('dead-4', 'completed'),
          chip('dead-5', 'completed')
        ]
      },
      row('paloma', 'paused')
    ])

    // Cards land in their columns.
    expect(await screen.findByText('verity')).toBeTruthy()
    expect(screen.getByText('hermes')).toBeTruthy()
    expect(screen.getByText('paloma')).toBeTruthy()

    // Column headers (archived is empty → collapsed to a rail, still labeled).
    expect(screen.getByText('Needs attention')).toBeTruthy()
    expect(screen.getByText('Active')).toBeTruthy()
    expect(screen.getByText('Paused')).toBeTruthy()
    expect(screen.getByText('Archived')).toBeTruthy()

    // Attention accents + latest update headline.
    expect(screen.getByText('controller blocked on CI')).toBeTruthy()
    expect(screen.getByText(/Shipping PR #42/)).toBeTruthy()

    // The card no longer renders per-mission chips — the drawer owns the
    // mission list. The health digest carries the counts instead.
    expect(screen.queryByText('Ship PR')).toBeNull()
    expect(screen.queryByText('dead-1')).toBeNull()

    dispose()
  })

  it('shows the decisions badge and autonomy chip when the row carries them', async () => {
    const { dispose } = renderBoard([
      {
        ...row('verity', 'attention'),
        attention_reasons: ['2 decisions awaiting you'],
        autonomy_level: 'act_reversible',
        pending_decisions: 2
      }
    ])

    expect(await screen.findByText('2 decisions')).toBeTruthy()
    expect(screen.getByText('acts (reversible)')).toBeTruthy()

    dispose()
  })

  it('renders plainly when the row predates the ledger fields', async () => {
    // Old backend: no autonomy_level, no pending_decisions — no chip, no badge.
    const { dispose } = renderBoard([row('verity', 'active')])

    expect(await screen.findByText('verity')).toBeTruthy()
    expect(screen.queryByText(/decision/)).toBeNull()

    dispose()
  })

  it('hides the surface politely when sandboxed.sh is absent (503)', async () => {
    const dispose = bindApi(() => Promise.reject(Object.assign(new Error('503'), { status: 503 })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <ProjectsBoardPage />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(await screen.findByText('No sandboxed.sh projects on this host.')).toBeTruthy()
    dispose()
  })
})
