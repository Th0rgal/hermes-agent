import { cleanup, render } from '@testing-library/react'
import type * as React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/hermes'

import { SidebarSessionsSection, VIRTUALIZE_THRESHOLD } from './sessions-section'
import type { VirtualSessionListProps } from './virtual-session-list'

afterEach(cleanup)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      sidebar: {
        dateDivider: {
          earlierThisMonth: 'Earlier this month',
          lastMonth: 'Last month',
          lastWeek: 'Last week',
          older: 'Older',
          today: 'Today',
          yesterday: 'Yesterday'
        }
      }
    }
  })
}))

const mockVirtualListPropsHistory: VirtualSessionListProps[] = []

vi.mock('./virtual-session-list', () => ({
  VirtualSessionList: (props: VirtualSessionListProps) => {
    mockVirtualListPropsHistory.push(props)

    return <div data-testid="virtual-session-list">Virtual List ({props.rows.length} rows)</div>
  }
}))

vi.mock('./session-row', () => ({
  SidebarSessionRow: ({
    isSelected,
    onPin,
    session
  }: {
    isSelected?: boolean
    onPin?: () => void
    session: SessionInfo
  }) => (
    <div data-selected={isSelected ? 'true' : undefined} data-testid={`session-row-${session.id}`} onClick={onPin}>
      {session.id}
    </div>
  )
}))

function makeSession(id: string, startedAt = 1000): SessionInfo {
  return {
    handoff_platform: null,
    handoff_state: null,
    id,
    last_active: startedAt,
    profile: 'default',
    started_at: startedAt
  } as unknown as SessionInfo
}

function generateSessions(count: number): SessionInfo[] {
  return Array.from({ length: count }, (_, i) => makeSession(`session-${i + 1}`, 10000 - i * 100))
}

const noop = () => {}

describe('SidebarSessionsSection memoization & virtualizer stability', () => {
  it('memoizes flatRows and passes the exact same rows array reference across parent re-renders', () => {
    mockVirtualListPropsHistory.length = 0

    const sessions = generateSessions(VIRTUALIZE_THRESHOLD + 5)

    const { rerender } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={sessions}
        workingSessionIdSet={new Set()}
      />
    )

    expect(mockVirtualListPropsHistory.length).toBe(1)
    const initialRowsRef = mockVirtualListPropsHistory[0].rows
    expect(initialRowsRef.length).toBeGreaterThan(VIRTUALIZE_THRESHOLD)

    // Re-render parent with the exact same sessions array and props
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={sessions}
        workingSessionIdSet={new Set()}
      />
    )

    expect(mockVirtualListPropsHistory.length).toBe(2)
    const nextRowsRef = mockVirtualListPropsHistory[1].rows

    // Confirm that the flatRows array reference remains strictly identical across renders (useMemo proof)
    expect(nextRowsRef).toBe(initialRowsRef)
  })

  it('re-computes flatRows reference when dateGrouped or sessions change', () => {
    mockVirtualListPropsHistory.length = 0

    const initialSessions = generateSessions(VIRTUALIZE_THRESHOLD + 2)

    const { rerender } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        dateGrouped={false}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={initialSessions}
        workingSessionIdSet={new Set()}
      />
    )

    const firstRowsRef = mockVirtualListPropsHistory[0].rows

    // Change dateGrouped to true
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        dateGrouped={true}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={initialSessions}
        workingSessionIdSet={new Set()}
      />
    )

    const secondRowsRef = mockVirtualListPropsHistory[1].rows
    expect(secondRowsRef).not.toBe(firstRowsRef)

    // Change sessions array identity
    const updatedSessions = generateSessions(VIRTUALIZE_THRESHOLD + 4)
    rerender(
      <SidebarSessionsSection
        activeSessionId={null}
        dateGrouped={true}
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={updatedSessions}
        workingSessionIdSet={new Set()}
      />
    )

    const thirdRowsRef = mockVirtualListPropsHistory[2].rows
    expect(thirdRowsRef).not.toBe(secondRowsRef)
  })
})

describe('pinned rows follow conversation lineage', () => {
  it('highlights a pinned TIP row when the focused id is the stored root (and vice versa)', async () => {
    // A pinned row renders the live tip of its chain while the focused id may
    // be either side of a compression rotation — selection must match through
    // the lineage, exactly like the projects-board rows.
    const { $sessions } = await import('@/store/session')

    const tip = makeSession('tip', 5000)

    ;(tip as unknown as { _lineage_root_id: string })._lineage_root_id = 'root'
    $sessions.set([tip])

    try {
      const { getByTestId, rerender } = render(
        <SidebarSessionsSection
          activeSessionId="root"
          emptyState={<div>Empty</div>}
          label="Pinned"
          onArchiveSession={noop}
          onDeleteSession={noop}
          onResumeSession={noop}
          onToggle={noop}
          onTogglePin={noop}
          open={true}
          pinned={true}
          sessions={[tip]}
          workingSessionIdSet={new Set()}
        />
      )

      expect(getByTestId('session-row-tip').dataset.selected).toBe('true')

      // An unrelated focused id must not select the pinned row.
      rerender(
        <SidebarSessionsSection
          activeSessionId="elsewhere"
          emptyState={<div>Empty</div>}
          label="Pinned"
          onArchiveSession={noop}
          onDeleteSession={noop}
          onResumeSession={noop}
          onToggle={noop}
          onTogglePin={noop}
          open={true}
          pinned={true}
          sessions={[tip]}
          workingSessionIdSet={new Set()}
        />
      )

      expect(getByTestId('session-row-tip').dataset.selected).toBeUndefined()
    } finally {
      $sessions.set([])
    }
  })

  it('keeps the pin toggle keyed on the DURABLE lineage id even when the row is the tip', () => {
    // Server pinned-flag semantics are keyed by the durable root id — a row
    // that resolved to the live tip must still pin/unpin under the root.
    const onTogglePin = vi.fn()

    const tip = makeSession('tip', 5000)

    ;(tip as unknown as { _lineage_root_id: string })._lineage_root_id = 'root'

    const { getByTestId } = render(
      <SidebarSessionsSection
        activeSessionId={null}
        emptyState={<div>Empty</div>}
        label="Pinned"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={onTogglePin}
        open={true}
        pinned={true}
        sessions={[tip]}
        workingSessionIdSet={new Set()}
      />
    )

    getByTestId('session-row-tip').click()

    expect(onTogglePin).toHaveBeenCalledWith('root')
  })

  it('keeps exact-id selection for unpinned rows', () => {
    const session = makeSession('plain')

    const { getByTestId } = render(
      <SidebarSessionsSection
        activeSessionId="plain"
        emptyState={<div>Empty</div>}
        label="Sessions"
        onArchiveSession={noop}
        onDeleteSession={noop}
        onResumeSession={noop}
        onToggle={noop}
        onTogglePin={noop}
        open={true}
        pinned={false}
        sessions={[session]}
        workingSessionIdSet={new Set()}
      />
    )

    expect(getByTestId('session-row-plain').dataset.selected).toBe('true')
  })
})
