import { host } from '@hermes/plugin-sdk'
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
  conversation: sessionId ? { session_id: sessionId, source: 'controller' } : null,
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
    // The old amber attention dot is gone — no row here has a stopped
    // controller, so no status icon renders either.
    expect(screen.queryByLabelText('Needs attention')).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()

    dispose()
  })

  it('shows the controller-status icon with its provenance, not an amber dot', async () => {
    const { dispose } = renderSection([
      // Operator paused via the board action → pause icon.
      { ...row('paloma', 'paused', 'sess-paloma'), override: 'paused' },
      // The controller stopped ITSELF → cut icon with the cause.
      { ...row('verity', 'attention', 'sess-verity'), mode: 'blocked:transport-cap' },
      // Silent for > 2h → cut icon, "silent since …".
      {
        ...row('lido', 'active', 'sess-lido'),
        latest_update: { at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(), headline: 'tick', mode: 'active' }
      },
      // Active and fresh → no icon.
      {
        ...row('hermes', 'active', 'sess-hermes'),
        latest_update: { at: new Date().toISOString(), headline: 'tick', mode: 'active' }
      }
    ])

    await screen.findByText('paloma')

    expect(screen.getByLabelText('Paused by you')).toBeTruthy()
    expect(screen.getByLabelText('Controller stopped itself: transport-cap')).toBeTruthy()
    expect(screen.getByLabelText(/Controller silent since/)).toBeTruthy()
    expect(screen.getAllByRole('img')).toHaveLength(3)

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

describe('row selection mirrors the core sidebar', () => {
  const sessionInfo = (id: string, lineageRoot: null | string = null) => ({
    archived: false,
    cwd: null,
    ended_at: null,
    id,
    _lineage_root_id: lineageRoot,
    input_tokens: 0,
    is_active: false,
    last_active: 0,
    message_count: 0,
    model: null,
    output_tokens: 0,
    preview: null,
    source: null,
    started_at: 0,
    title: null,
    tool_call_count: 0
  })

  const rowButton = (slug: string) => screen.getByText(slug).closest('button')!

  const SELECTED_CLASS = 'bg-(--ui-row-active-background)'

  it('selecting the TIP highlights the row bound to the durable id (and only it)', async () => {
    const { setSessions, setSelectedStoredSessionId } = await import('@/store/session')

    setSessions([sessionInfo('tip-verity', 'root-verity')])

    const { dispose } = renderSection([
      row('verity', 'active', 'root-verity'),
      row('paloma', 'active', 'sess-paloma')
    ])

    await screen.findByText('verity')
    setSelectedStoredSessionId('tip-verity')

    await waitFor(() => expect(rowButton('verity').className).toContain(SELECTED_CLASS))
    expect(rowButton('paloma').className).not.toContain(SELECTED_CLASS)

    setSelectedStoredSessionId(null)
    setSessions([])
    dispose()
  })

  it('selecting the durable id highlights the row bound to the TIP', async () => {
    const { setSessions, setSelectedStoredSessionId } = await import('@/store/session')

    setSessions([sessionInfo('tip-verity', 'root-verity')])

    const { dispose } = renderSection([row('verity', 'active', 'tip-verity')])

    await screen.findByText('verity')
    setSelectedStoredSessionId('root-verity')

    await waitFor(() => expect(rowButton('verity').className).toContain(SELECTED_CLASS))

    // Deselecting clears the highlight.
    setSelectedStoredSessionId(null)
    await waitFor(() => expect(rowButton('verity').className).not.toContain(SELECTED_CLASS))

    setSessions([])
    dispose()
  })
})

describe('delete project from the row menu', () => {
  it('renders Delete in the context menu, gates on confirm, then posts the action', async () => {
    const calls: Array<{ body?: unknown; path: string }> = []

    const rest = <T,>(path: string, opts?: { body?: unknown }): Promise<T> => {
      calls.push({ body: opts?.body, path })

      if (path === '/projects') {
        return Promise.resolve({ projects: [row('verity', 'active', 'sess-verity')] } as T)
      }

      return Promise.resolve({} as T)
    }

    const dispose = bindApi(rest as Parameters<typeof bindApi>[0])
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const view = render(
      <QueryClientProvider client={client}>
        <ProjectsSidebarSection />
      </QueryClientProvider>
    )

    fireEvent.contextMenu(await screen.findByText('verity'))

    // Destructive entry present; selecting it opens the confirm — no call yet.
    fireEvent.click(await screen.findByText('Delete project'))
    expect(await screen.findByText('Delete project verity?')).toBeTruthy()
    expect(calls.some(c => c.path.includes('/action'))).toBe(false)

    // Confirming fires exactly the delete action.
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() =>
      expect(calls.some(c => c.path === '/projects/verity/action' && JSON.stringify(c.body) === '{"action":"delete"}')).toBe(
        true
      )
    )

    view.unmount()
    dispose()
  })
})
