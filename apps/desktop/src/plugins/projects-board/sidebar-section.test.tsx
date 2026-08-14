import { $activeChatSessionIds, $projectBoundSessionIds, host } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { bindApi, type ProjectRow } from './api'
import { BOARD_LOCALES } from './i18n'
import { ProjectsSidebarSection } from './sidebar-section'

beforeAll(async () => {
  // The host registers plugin bundles via ctx.i18n; the test reaches the same
  // registry directly (dynamic import — plugin sources import only the SDK).
  const { registerPluginLocales } = await import('@/i18n/plugin-i18n')

  registerPluginLocales('projects-board', BOARD_LOCALES)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const row = (slug: string, bucket: string, sessionId?: string): ProjectRow => ({
  attention_reasons: bucket === 'attention' ? ['blocked on CI'] : [],
  bucket,
  conversation: sessionId ? { session_id: sessionId, source: 'binding' } : null,
  latest_update: null,
  missions: [],
  slug
})

function renderSection(projects: ProjectRow[] | (() => Promise<never>)) {
  const rest = typeof projects === 'function' ? projects : <T,>() => Promise.resolve({ projects } as T)
  const dispose = bindApi(rest as Parameters<typeof bindApi>[0])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  const view = render(
    <QueryClientProvider client={client}>
      <ProjectsSidebarSection />
    </QueryClientProvider>
  )

  return { dispose, view }
}

describe('the Projects sidebar section', () => {
  it('lists only non-archived projects with a bound conversation', async () => {
    const { dispose } = renderSection([
      row('verity', 'attention', 'sess-verity'),
      row('hermes', 'active', 'sess-hermes'),
      row('old', 'archived', 'sess-old'),
      row('unbound', 'active')
    ])

    expect(await screen.findByText('verity')).toBeTruthy()
    expect(screen.getByText('hermes')).toBeTruthy()
    expect(screen.getByText('Projects')).toBeTruthy()
    // Archived and binding-less projects never surface.
    expect(screen.queryByText('old')).toBeNull()
    expect(screen.queryByText('unbound')).toBeNull()
    // The attention hint rides only the attention row.
    expect(screen.getByLabelText('Needs attention')).toBeTruthy()

    dispose()
  })

  it('navigates to the bound session on click', async () => {
    const navigate = vi.spyOn(host, 'navigate').mockImplementation(() => undefined)
    const { dispose } = renderSection([row('verity', 'active', 'sess verity')])

    fireEvent.click(await screen.findByText('verity'))

    // Session routes are '/<encoded session id>' (routes.ts sessionRoute).
    expect(navigate).toHaveBeenCalledWith('/sess%20verity')
    dispose()
  })

  it('shows an unread badge for new messages since last open, capped at 9+', async () => {
    const { setSessions } = await import('@/store/session')
    const { $sessionLastSeenCounts } = await import('@/store/session-unread')

    const sessionInfo = (id: string, messageCount: number) => ({
      archived: false,
      cwd: null,
      ended_at: null,
      id,
      _lineage_root_id: null,
      input_tokens: 0,
      is_active: false,
      last_active: 0,
      message_count: messageCount,
      model: null,
      output_tokens: 0,
      preview: null,
      source: null,
      started_at: 0,
      title: null,
      tool_call_count: 0
    })

    setSessions([sessionInfo('sess-verity', 12), sessionInfo('sess-hermes', 40)])
    $sessionLastSeenCounts.set({ 'sess-hermes': 9, 'sess-verity': 9 })

    const { dispose } = renderSection([
      row('verity', 'active', 'sess-verity'),
      row('hermes', 'active', 'sess-hermes')
    ])

    // 12 − 9 = 3 unread; 40 − 9 = 31 → capped presentation.
    expect(await screen.findByText('3')).toBeTruthy()
    expect(screen.getByText('9+')).toBeTruthy()
    expect(screen.getByLabelText('3 unread messages')).toBeTruthy()

    // Opening the session (the core selection signal) clears the badge.
    const { setSelectedStoredSessionId } = await import('@/store/session')

    setSelectedStoredSessionId('sess-verity')
    setSelectedStoredSessionId(null)

    await waitFor(() => expect(screen.queryByText('3')).toBeNull())

    setSessions([])
    $sessionLastSeenCounts.set({})
    dispose()
  })

  it('resolves the same session color the core sidebar rows show', async () => {
    const { setSessions } = await import('@/store/session')
    const { setSessionColorOverride } = await import('@/store/session-color')

    setSessions([])
    // An id-only binding: the override keyed by the durable id must reach the
    // dot even though the session is not in the loaded recents page.
    setSessionColorOverride('sess-verity', 'rgb(10, 200, 100)')

    const { dispose, view } = renderSection([row('verity', 'active', 'sess-verity')])

    await screen.findByText('verity')

    const dots = [...view.container.querySelectorAll('span')].filter(
      el => el.style.backgroundColor === 'rgb(10, 200, 100)'
    )

    expect(dots.length).toBeGreaterThan(0)

    setSessionColorOverride('sess-verity', null)
    dispose()
  })

  it('renders the bound row selected when its session is the open chat', async () => {
    $activeChatSessionIds.set(['sess-verity'])

    const { dispose } = renderSection([
      row('verity', 'active', 'sess-verity'),
      row('hermes', 'active', 'sess-hermes')
    ])

    const selectedRow = (await screen.findByText('verity')).closest('button')!
    const idleRow = screen.getByText('hermes').closest('button')!

    expect(selectedRow.className).toContain('bg-(--ui-row-active-background)')
    expect(idleRow.className).not.toContain('bg-(--ui-row-active-background)')
    $activeChatSessionIds.set([])
    dispose()
  })

  it('publishes bindings for the core Recents dedup, and clears on dispose', async () => {
    const { dispose } = renderSection([
      row('verity', 'active', 'sess-verity'),
      row('lido', 'archived', 'sess-lido')
    ])

    await screen.findByText('verity')

    // Archived projects release their session back to the flat list.
    expect($projectBoundSessionIds.get()).toEqual({ 'sess-verity': 'verity' })
    dispose()
    expect($projectBoundSessionIds.get()).toEqual({})
  })

  it('opens the project card in place from the row menu', async () => {
    const { dispose } = renderSection([row('verity', 'active', 'sess-verity')])
    await screen.findByText('verity')

    fireEvent.contextMenu(screen.getByText('verity'))
    fireEvent.click(await screen.findByText('Open board card'))

    // The board card dialog opens over the current view — no navigation.
    const navigate = vi.spyOn(host, 'navigate')

    await screen.findByRole('dialog')
    expect(navigate).not.toHaveBeenCalled()
    dispose()
  })

  it('hides itself entirely when the surface is unavailable (503)', async () => {
    const { dispose, view } = renderSection(() =>
      Promise.reject(Object.assign(new Error('503'), { status: 503 }))
    )

    // Let the query settle; the section must render nothing at all.
    await Promise.resolve()
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(view.container.textContent).toBe('')
    dispose()
  })
})
