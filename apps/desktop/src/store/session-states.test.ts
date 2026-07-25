import { beforeEach, describe, expect, it } from 'vitest'

import { group, split } from '@/components/pane-shell/tree/model'
import { createClientSessionState } from '@/lib/chat-runtime'
import { $unreadFinishedSessionIds, setSelectedStoredSessionId } from '@/store/session'
import type { SessionTile } from '@/store/session-states'
import {
  clearAllSessionStates,
  markSilentSessionActivity,
  orderTilesByTree,
  publishSessionState,
  selectionHomesToWorkspace
} from '@/store/session-states'

const tile = (storedSessionId: string): SessionTile => ({ storedSessionId })
const tilePane = (id: string) => `session-tile:${id}`

describe('orderTilesByTree', () => {
  it('no-ops (null) without a tree or below two tiles', () => {
    expect(orderTilesByTree(null, [tile('a'), tile('b')])).toBeNull()
    expect(orderTilesByTree(group([tilePane('a')]), [tile('a')])).toBeNull()
  })

  it('reorders tiles to layout-tree encounter order across a split', () => {
    const tree = split('row', [group(['workspace', tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('a'), tile('b')])).toEqual([tile('b'), tile('a')])
  })

  it('returns null when the array already matches strip order (skip persist)', () => {
    const tree = split('row', [group([tilePane('b')]), group([tilePane('a')])])

    expect(orderTilesByTree(tree, [tile('b'), tile('a')])).toBeNull()
  })

  it('sorts not-yet-adopted tiles after placed ones, stably', () => {
    const tree = group(['workspace', tilePane('b')])

    expect(orderTilesByTree(tree, [tile('a'), tile('b'), tile('c')])).toEqual([tile('b'), tile('a'), tile('c')])
  })
})

describe('selectionHomesToWorkspace', () => {
  const tiles = [tile('a'), tile('b')]

  it('homes for a null selection or a non-tile session', () => {
    expect(selectionHomesToWorkspace(null, tiles)).toBe(true)
    expect(selectionHomesToWorkspace('c', tiles)).toBe(true)
  })

  it('skips homing when the selected id is already an open tile', () => {
    expect(selectionHomesToWorkspace('a', tiles)).toBe(false)
  })
})

describe('markSilentSessionActivity', () => {
  beforeEach(() => {
    clearAllSessionStates()
    $unreadFinishedSessionIds.set([])
    setSelectedStoredSessionId(null)
  })

  const row = (id: string, message_count: number) => ({ id, message_count })

  it('marks a session unread when its transcript grew without a turn (delivery)', () => {
    markSilentSessionActivity([row('s1', 10), row('s2', 4)], [row('s1', 12), row('s2', 4)])

    expect($unreadFinishedSessionIds.get()).toEqual(['s1'])
  })

  it('skips the selected session — the user is already looking at it', () => {
    setSelectedStoredSessionId('s1')
    markSilentSessionActivity([row('s1', 10)], [row('s1', 12)])

    expect($unreadFinishedSessionIds.get()).toEqual([])
  })

  it('skips a working session — its settle transition owns the unread cue', () => {
    publishSessionState('rt-1', { ...createClientSessionState('s1'), busy: true })
    markSilentSessionActivity([row('s1', 10)], [row('s1', 12)])

    expect($unreadFinishedSessionIds.get()).toEqual([])
  })

  it('needs a baseline — a first fetch or a brand-new row marks nothing', () => {
    markSilentSessionActivity([], [row('s1', 12)])
    markSilentSessionActivity([row('s2', 1)], [row('s1', 12), row('s2', 1)])

    expect($unreadFinishedSessionIds.get()).toEqual([])
  })

  it('does not duplicate an already-unread session', () => {
    $unreadFinishedSessionIds.set(['s1'])
    markSilentSessionActivity([row('s1', 10)], [row('s1', 12)])

    expect($unreadFinishedSessionIds.get()).toEqual(['s1'])
  })
})
