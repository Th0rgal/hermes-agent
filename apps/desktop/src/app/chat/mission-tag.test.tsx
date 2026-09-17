import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SessionMission } from '@/hermes'

import { MissionTag } from './mission-tag'

function mission(overrides: Partial<SessionMission> = {}): SessionMission {
  return {
    created_at: '2026-08-04T10:00:00Z',
    id: '11111111-2222-3333-4444-555555555555',
    project: null,
    short_description: null,
    status: 'completed',
    title: 'Fix CI',
    track: null,
    updated_at: '2026-08-04T11:00:00Z',
    ...overrides
  }
}

describe('MissionTag', () => {
  it('renders nothing when the gateway could not answer', () => {
    // Null is "I could not ask" — an unconfigured or unreachable backend. It
    // must not become a "0" that reads as a fact about the conversation.
    const { container } = render(<MissionTag missions={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders nothing for a conversation with no missions', () => {
    const { container } = render(<MissionTag missions={[]} />)
    expect(container.innerHTML).toBe('')
  })

  it('leads with what needs attention', () => {
    render(
      <MissionTag
        missions={[mission(), mission({ id: 'b', status: 'failed' }), mission({ id: 'c', status: 'active' })]}
      />
    )
    expect(screen.getByRole('img').textContent).toBe('1!')
  })

  it('shows work in flight when nothing is broken', () => {
    render(<MissionTag missions={[mission(), mission({ id: 'b', status: 'active' })]} />)
    expect(screen.getByRole('img').textContent).toBe('1▸')
  })

  it('does not count a mission parked for a human as running', () => {
    // awaiting_user means the turn ended and nothing moves until someone
    // reads it; counting it as running hides a stalled conversation.
    render(<MissionTag missions={[mission({ status: 'awaiting_user' })]} />)
    expect(screen.getByRole('img').textContent).toBe('1')
  })

  it('names the project and track in the tooltip', () => {
    render(<MissionTag missions={[mission({ project: 'verity', track: 'phase1d/core-c3' })]} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('verity · phase1d/core-c3')
  })

  it('falls back to a short id when a mission has no title', () => {
    render(<MissionTag missions={[mission({ title: null })]} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('11111111')
  })
})
