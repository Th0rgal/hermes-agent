import { describe, expect, it } from 'vitest'

import {
  isStaleControllerHeadline,
  itemAsRoadmapTask,
  leadSignal,
  type ProjectDetail,
  type ProjectRow,
  railOpenItems,
  roadmapFromItems
} from './api'

const row = (partial: Partial<ProjectRow>): ProjectRow => ({
  attention_reasons: [],
  bucket: 'attention',
  latest_update: null,
  missions: [],
  slug: 'verity-core',
  ...partial
})

describe('leadSignal', () => {
  it('drops a lease-writer headline while a writer is live', () => {
    const signal = leadSignal(
      row({
        health: { active: 2, failed: 5, missions: 22, tracks_needing_attention: 3 },
        latest_update: {
          at: '2026-08-14T14:30:54Z',
          headline: 'Verity #2332 — BLOQUÉE PAR LEASE WRITER',
          mode: 'active'
        },
        missions: [
          {
            github_pr: null,
            id: '08306fdb',
            status: 'active',
            title: 'Grok #2332',
            updated_at: '2026-08-14T21:08:22Z'
          }
        ],
        next_action: 'rebase/repair #2332 onto main after #2333'
      })
    )

    expect(signal.headline).toBe('rebase/repair #2332 onto main after #2333')
    expect(signal.nextAction).toBe('rebase/repair #2332 onto main after #2333')
    expect(signal.controllerBehind).toBe(true)
    expect(signal.liveCount).toBe(2)
    expect(isStaleControllerHeadline('BLOQUÉE PAR LEASE WRITER', 2)).toBe(true)
  })

  it('keeps a lease headline when nothing is live', () => {
    const signal = leadSignal(
      row({
        health: { active: 0, failed: 1, missions: 1, tracks_needing_attention: 1 },
        latest_update: {
          at: '2026-08-14T14:30:54Z',
          headline: 'BLOQUÉE PAR LEASE WRITER',
          mode: 'blocked'
        }
      })
    )

    expect(signal.headline).toBe('BLOQUÉE PAR LEASE WRITER')
    expect(signal.controllerBehind).toBe(false)
  })
})

describe('railOpenItems', () => {
  it('ranks live items first and caps the graveyard', () => {
    const detail: ProjectDetail = {
      items: [
        {
          attempts: [{ id: 'old', status: 'failed', updated_at: '2026-08-01T00:00:00Z' }],
          key: 'old-fail',
          open: true
        },
        {
          attempts: [
            { id: 'live', status: 'active', title: 'Grok #2332', updated_at: '2026-08-14T21:08:22Z' }
          ],
          key: 'c5-preflight-pr2332',
          open: true
        },
        ...Array.from({ length: 20 }, (_, index) => ({
          attempts: [{ id: `f${index}`, status: 'interrupted', updated_at: '2026-08-01T00:00:00Z' }],
          key: `stale-${index}`,
          open: true
        }))
      ],
      project: { slug: 'verity-core', status: 'active' }
    }

    const items = railOpenItems(detail, 8)

    expect(items).toHaveLength(8)
    expect(items[0]?.key).toBe('c5-preflight-pr2332')
    expect(items[0]?.live).toBe(true)
    expect(items.some(item => item.key === 'stale-19')).toBe(false)
  })

  it('maps items onto the single roadmap list', () => {
    const detail: ProjectDetail = {
      items: [
        {
          attempts: [
            { id: 'live', status: 'active', title: 're-pin Verity', updated_at: '2026-08-15T10:00:00Z' }
          ],
          desired_state: 'proven Verity tx',
          key: 'lido-verity-closure-v2',
          open: true
        },
        {
          attempts: [],
          desired_state: 'old PR 46',
          key: 'certify-pr46',
          open: false,
          status: 'done'
        }
      ],
      project: { slug: 'verity-lido', status: 'active' }
    }

    const live = itemAsRoadmapTask(detail.items![0])
    expect(live.status).toBe('running')
    expect(live.title).toBe('proven Verity tx')
    expect(live.task_key).toBe('lido-verity-closure-v2')

    const roadmap = roadmapFromItems(detail)
    expect(roadmap.summary).toEqual({ done: 1, failed: 0, running: 1, total: 2 })
    expect(roadmap.tasks.map(task => task.task_key)).toEqual([
      'lido-verity-closure-v2',
      'certify-pr46'
    ])
  })
})
