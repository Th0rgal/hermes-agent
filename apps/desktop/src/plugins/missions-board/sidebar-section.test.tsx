import { host } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { bindApi, type ProjectRow } from './api'
import { BOARD_LOCALES } from './i18n'
import { ProjectsSidebarSection } from './sidebar-section'

beforeAll(async () => {
  // The host registers plugin bundles via ctx.i18n; the test reaches the same
  // registry directly (dynamic import — plugin sources import only the SDK).
  const { registerPluginLocales } = await import('@/i18n/plugin-i18n')

  registerPluginLocales('missions-board', BOARD_LOCALES)
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
