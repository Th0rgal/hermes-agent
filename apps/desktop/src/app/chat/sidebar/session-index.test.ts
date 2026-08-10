import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { buildSessionByAnyId, pinnedDisplaySession } from './session-index'

const row = (id: string, extra: Partial<SessionInfo> = {}): SessionInfo =>
  ({ id, message_count: 1, source: 'cli', started_at: 0, title: id, ...extra }) as SessionInfo

describe('buildSessionByAnyId', () => {
  it('resolves a pin from every slice the sidebar fetches', () => {
    // The contract that matters: a pin is looked up in this map no matter
    // which slice owns the row. Messaging is the one that regressed — a
    // pinned session is filtered out of its own section, so a miss here
    // removes it from the sidebar entirely rather than just misplacing it.
    const index = buildSessionByAnyId([row('recent')], [row('cron_job_1')], [row('telegram_42')])

    for (const id of ['recent', 'cron_job_1', 'telegram_42']) {
      expect(index.get(id)?.id).toBe(id)
    }
  })

  it('resolves a pin stored on the pre-compression lineage root', () => {
    const index = buildSessionByAnyId([], [], [row('tip', { _lineage_root_id: 'root' })])

    // Both identities point at the one live row.
    expect(index.get('root')?.id).toBe('tip')
    expect(index.get('tip')?.id).toBe('tip')
  })

  it('lets a recents row win a direct id collision', () => {
    const index = buildSessionByAnyId(
      [row('dupe', { title: 'from recents' })],
      [],
      [row('dupe', { title: 'from messaging' })]
    )

    expect(index.get('dupe')?.title).toBe('from recents')
  })

  it('resolves the LIVE TIP when the stored root row and its continuation are both loaded', () => {
    // The pinned-row staleness bug: a pin stored on the durable root id kept
    // resolving to the root's own (frozen) row while work continued in the
    // compression continuation. Every key of the chain must resolve to the
    // freshest row — title, activity dot, and navigation then follow the tip.
    const root = row('root', { last_active: 10 })
    const tip = row('tip', { _lineage_root_id: 'root', last_active: 20 })

    for (const index of [
      buildSessionByAnyId([root, tip], [], []),
      buildSessionByAnyId([tip, root], [], []),
      buildSessionByAnyId([root], [], [tip])
    ]) {
      expect(index.get('root')?.id).toBe('tip')
      expect(index.get('tip')?.id).toBe('tip')
    }
  })

  it('keeps the fresher row of a chain even when it is the root itself', () => {
    // Same-chain resolution is by recency, not by role — a root row that is
    // genuinely fresher than a stale alias keeps its key.
    const index = buildSessionByAnyId([row('root', { last_active: 30 })], [], [
      row('tip', { _lineage_root_id: 'root', last_active: 20 })
    ])

    expect(index.get('root')?.id).toBe('root')
  })

  it('folds individually fetched rows in at lowest priority', () => {
    // A pin whose chain rolled past every loaded page resolves through the
    // fetched slice; a loaded row of a DIFFERENT chain still wins its own id.
    const index = buildSessionByAnyId([row('dupe', { title: 'loaded' })], [], [], [
      row('tip', { _lineage_root_id: 'root', last_active: 5 }),
      row('dupe', { title: 'fetched' })
    ])

    expect(index.get('root')?.id).toBe('tip')
    expect(index.get('dupe')?.title).toBe('loaded')
  })
})

describe('pinnedDisplaySession', () => {
  it('keeps the root\'s custom name when the chain resolved to an auto-titled tip', () => {
    const root = row('root', { title: 'Verity' })
    const tip = row('tip', { _lineage_root_id: 'root', title: 'Untitled session' })
    const exact = new Map([[root.id, root], [tip.id, tip]])

    const shown = pinnedDisplaySession('root', tip, exact)

    // Display: the user's rename. Navigation/activity: still the tip's row.
    expect(shown.title).toBe('Verity')
    expect(shown.id).toBe('tip')
  })

  it('falls back to the tip title when the root has none (or is not loaded)', () => {
    const tip = row('tip', { _lineage_root_id: 'root', title: 'Sandboxed manager #4' })

    expect(pinnedDisplaySession('root', tip, new Map([[tip.id, tip]])).title).toBe('Sandboxed manager #4')
    expect(
      pinnedDisplaySession('root', tip, new Map([['root', row('root', { title: '   ' })]])).title
    ).toBe('Sandboxed manager #4')
  })

  it('is identity when the pin resolves to its own row', () => {
    const root = row('root', { title: 'Verity' })

    expect(pinnedDisplaySession('root', root, new Map())).toBe(root)
  })
})
