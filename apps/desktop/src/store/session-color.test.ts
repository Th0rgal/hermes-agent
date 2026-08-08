import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProjectInfo, SessionInfo } from '@/types/hermes'

import { $projects } from './projects'
import { $cronSessions, $messagingSessions, $sessions } from './session'
import {
  $fetchedSessionsById,
  $sessionColorById,
  $sessionColorOverrides,
  resetLineageFetchState,
  sessionColorFor,
  sessionColorForId,
  sessionDurableId,
  setSessionColorOverride
} from './session-color'

const getSession = vi.fn()

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getSession: (...args: unknown[]) => getSession(...args)
}))

let nextId = 0

function makeSession(cwd: null | string, overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    archived: false,
    cwd,
    ended_at: null,
    id: `s${nextId++}`,
    input_tokens: 0,
    is_active: false,
    last_active: 1_000,
    message_count: 1,
    model: 'claude',
    output_tokens: 0,
    preview: null,
    source: 'cli',
    started_at: 1_000,
    title: null,
    tool_call_count: 0,
    ...overrides
  }
}

function makeProject(id: string, folders: string[], color: null | string): ProjectInfo {
  return {
    archived: false,
    board_slug: null,
    color,
    created_at: 0,
    description: null,
    folders: folders.map((path, i) => ({ added_at: 0, is_primary: i === 0, label: null, path })),
    icon: null,
    id,
    name: id,
    primary_path: folders[0] ?? null,
    slug: id
  }
}

afterEach(() => {
  $sessions.set([])
  $cronSessions.set([])
  $messagingSessions.set([])
  $projects.set([])
  $sessionColorOverrides.set({})
})

describe('$sessionColorById', () => {
  it('maps each session under a colored project to that color, keyed by live id', () => {
    const a = makeSession('/www/app/src', { git_repo_root: '/www/app' })
    const b = makeSession('/other/place')

    $projects.set([makeProject('p_app', ['/www/app'], '#4a9eff')])
    $sessions.set([a, b])

    const map = $sessionColorById.get()

    expect(map[a.id]).toBe('#4a9eff')
    // Sessions with no colored project are absent (a sparse map, not null-filled).
    expect(b.id in map).toBe(false)
  })

  it('omits a session whose project has no color', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app' })

    $projects.set([makeProject('p_app', ['/www/app'], null)])
    $sessions.set([a])

    expect(a.id in $sessionColorById.get()).toBe(false)
  })

  it('recomputes when the projects list changes (color applied later)', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app' })

    $sessions.set([a])
    $projects.set([makeProject('p_app', ['/www/app'], null)])
    expect($sessionColorById.get()[a.id]).toBeUndefined()

    $projects.set([makeProject('p_app', ['/www/app'], '#7bc86c')])
    expect($sessionColorById.get()[a.id]).toBe('#7bc86c')
  })
})

describe('$sessionColorOverrides', () => {
  it('an override wins over the inherited project color', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app' })

    $projects.set([makeProject('p_app', ['/www/app'], '#4a9eff')])
    $sessions.set([a])
    setSessionColorOverride(a.id, '#ff0000')

    expect($sessionColorById.get()[a.id]).toBe('#ff0000')
  })

  it('clearing an override falls back to the project color', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app' })

    $projects.set([makeProject('p_app', ['/www/app'], '#4a9eff')])
    $sessions.set([a])

    setSessionColorOverride(a.id, '#ff0000')
    expect($sessionColorById.get()[a.id]).toBe('#ff0000')

    setSessionColorOverride(a.id, null)
    expect($sessionColorById.get()[a.id]).toBe('#4a9eff')
  })

  it('keys on the durable lineage id so a color survives compression', () => {
    // The live id rotates on auto-compression; the override is stored against the
    // lineage root, so the continuation tip still resolves to the same color.
    const root = makeSession('/x', { id: 'root' })
    const tip = makeSession('/x', { id: 'tip', _lineage_root_id: 'root' })

    setSessionColorOverride('root', '#abcdef')

    $sessions.set([tip])
    expect($sessionColorById.get().tip).toBe('#abcdef')
  })
})

describe('sessionColorFor', () => {
  it('reads a single session through the same shared map', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app' })

    $projects.set([makeProject('p_app', ['/www/app'], '#5865f2')])
    $sessions.set([a])

    expect(sessionColorFor(a)).toBe('#5865f2')
  })

  it('returns undefined for a null/absent session', () => {
    expect(sessionColorFor(null)).toBeUndefined()
    expect(sessionColorFor(undefined)).toBeUndefined()
  })
})

describe('sessionColorForId — live conversation chains', () => {
  afterEach(() => {
    $fetchedSessionsById.set({})
    resetLineageFetchState()
    getSession.mockReset()
  })

  it('bridges a live tip the loaded lists do not carry to its stored override', async () => {
    // The pinned/stored side of the chain: override keyed under the durable id.
    setSessionColorOverride('root', '#abcdef')
    // The recents page only knows an OLDER continuation — not the live tip.
    $sessions.set([makeSession('/x', { id: 'older-tip', _lineage_root_id: 'root' })])

    getSession.mockResolvedValue(makeSession('/x', { id: 'live-tip', _lineage_root_id: 'root' }))

    // First ask: unknown — but it kicks off the one-shot lineage fetch.
    expect(sessionColorForId('live-tip')).toBeUndefined()
    expect(getSession).toHaveBeenCalledWith('live-tip')

    await vi.waitFor(() => expect($fetchedSessionsById.get()['live-tip']).toBeTruthy())

    // Resolved: the tip inherits the chain's color, and appears in the shared
    // map so every SessionStatusDot subscriber repaints.
    expect(sessionColorForId('live-tip')).toBe('#abcdef')
    expect($sessionColorById.get()['live-tip']).toBe('#abcdef')

    // Setting a color FROM the tip writes under the chain's durable id.
    expect(sessionDurableId('live-tip')).toBe('root')
  })

  it('remembers a failed resolution instead of refetching forever', async () => {
    getSession.mockRejectedValue(new Error('404'))

    expect(sessionColorForId('ghost')).toBeUndefined()
    await Promise.resolve()
    await Promise.resolve()
    expect(sessionColorForId('ghost')).toBeUndefined()

    expect(getSession).toHaveBeenCalledTimes(1)
  })
})

// The invariant: any id whose Pinned row shows color X must resolve to X from
// the id ALONE. Pinned rows come from $sessions ∪ $cronSessions ∪
// $messagingSessions (buildSessionByAnyId), so id-only resolution must see all
// three slices — an INHERITED project color needs the row's cwd, which no
// override lookup can substitute for.
describe('sessionColorForId — inherited colors across the sidebar slices', () => {
  it('resolves an inherited project color with NO override (row in $sessions)', () => {
    const a = makeSession('/www/app', { git_repo_root: '/www/app', id: 'lean-silicon' })

    $projects.set([makeProject('p_lean', ['/www/app'], '#e11d48')])
    $sessions.set([a])

    expect(sessionColorForId('lean-silicon')).toBe('#e11d48')
  })

  it('resolves an inherited color for a row that lives ONLY in the messaging slice', () => {
    // A controller/webhook conversation: pinned (so its row is on screen, in
    // color) but absent from the recents page.
    const a = makeSession('/www/verity', { git_repo_root: '/www/verity', id: 'verity-core' })

    $projects.set([makeProject('p_verity', ['/www/verity'], '#14b8a6')])
    $messagingSessions.set([a])

    expect(sessionColorForId('verity-core')).toBe('#14b8a6')
    // And through the shared map, so SessionStatusDot subscribers repaint.
    expect($sessionColorById.get()['verity-core']).toBe('#14b8a6')
  })

  it('resolves an inherited color for a cron-slice row', () => {
    const a = makeSession('/www/bench', { git_repo_root: '/www/bench', id: 'verity-benchmark' })

    $projects.set([makeProject('p_bench', ['/www/bench'], '#d946ef')])
    $cronSessions.set([a])

    expect(sessionColorForId('verity-benchmark')).toBe('#d946ef')
  })
})
