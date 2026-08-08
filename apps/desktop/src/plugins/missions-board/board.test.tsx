import { TooltipProvider } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import {
  bindApi,
  bucketAction,
  type MissionChip,
  moveProject,
  type ProjectRow,
  type ProjectsResponse,
  selectChips
} from './api'
import { MissionsBoardPage } from './board'
import { BOARD_LOCALES } from './i18n'

// Radix calls these on open; jsdom doesn't implement them.
beforeAll(async () => {
  // The host registers plugin bundles via ctx.i18n; the test reaches the same
  // registry directly. Dynamic import: plugin SOURCE files import only the
  // SDK, and the registration door isn't (yet) SDK-exported.
  const { registerPluginLocales } = await import('@/i18n/plugin-i18n')

  registerPluginLocales('missions-board', BOARD_LOCALES)
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
          <MissionsBoardPage />
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

    // Chip cap: 3 rendered (live + most-recent failed), the dead 5 folded
    // into one muted "+5" — never a wall of stopped/interrupted chips.
    expect(screen.getByText('Ship PR')).toBeTruthy()
    expect(screen.getByText('f1')).toBeTruthy()
    expect(screen.getByText('f2')).toBeTruthy()
    expect(screen.getByText('+5')).toBeTruthy()
    expect(screen.queryByText('dead-1')).toBeNull()

    dispose()
  })

  it('hides the surface politely when sandboxed.sh is absent (503)', async () => {
    const dispose = bindApi(() => Promise.reject(Object.assign(new Error('503'), { status: 503 })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <MissionsBoardPage />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(await screen.findByText('No sandboxed.sh projects on this host.')).toBeTruthy()
    dispose()
  })
})
