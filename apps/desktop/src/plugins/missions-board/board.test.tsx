import { TooltipProvider } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import {
  bindApi,
  bucketAction,
  moveProject,
  type ProjectRow,
  type ProjectsResponse
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
        missions: [{ github_pr: null, id: 'm2', status: 'active', title: 'Ship PR', updated_at: null }]
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
