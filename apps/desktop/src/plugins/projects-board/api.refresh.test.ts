import { $projectBoundSessionIds, queryClient } from '@hermes/plugin-sdk'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  gatewayEventRefreshesBoard,
  invalidateBoardQueries,
  PROJECTS_KEY,
  projectKey,
  projectToolRefreshesBoard,
  stateKey,
  tasksKey
} from './api'

afterEach(() => {
  $projectBoundSessionIds.set({})
  vi.restoreAllMocks()
})

describe('projectToolRefreshesBoard', () => {
  it('matches the project inventory MCP verbs, including namespaced tools', () => {
    expect(projectToolRefreshesBoard('plan_project_tasks')).toBe(true)
    expect(projectToolRefreshesBoard('assistant-mcp__plan_project_tasks')).toBe(true)
    expect(projectToolRefreshesBoard('get_project')).toBe(true)
    expect(projectToolRefreshesBoard('terminal')).toBe(false)
    expect(projectToolRefreshesBoard('write_file')).toBe(false)
  })
})

describe('gatewayEventRefreshesBoard', () => {
  it('refreshes the bound project when its chat turn settles', () => {
    $projectBoundSessionIds.set({ 'rt-lido': 'lido-audit' })

    expect(gatewayEventRefreshesBoard({ session_id: 'rt-lido', type: 'message.complete' })).toBe('lido-audit')
    expect(
      gatewayEventRefreshesBoard({
        payload: { running: false },
        session_id: 'rt-lido',
        type: 'session.info'
      })
    ).toBe('lido-audit')
  })

  it('ignores turn-end on an unbound chat', () => {
    expect(gatewayEventRefreshesBoard({ session_id: 'rt-other', type: 'message.complete' })).toBeNull()
    expect(
      gatewayEventRefreshesBoard({
        payload: { running: false },
        session_id: 'rt-other',
        type: 'session.info'
      })
    ).toBeNull()
  })

  it('refreshes on a project-inventory tool even mid-turn', () => {
    $projectBoundSessionIds.set({ 'rt-lido': 'lido-audit' })

    expect(
      gatewayEventRefreshesBoard({
        payload: { name: 'plan_project_tasks' },
        session_id: 'rt-lido',
        type: 'tool.complete'
      })
    ).toBe('lido-audit')
  })
})

describe('invalidateBoardQueries', () => {
  it('invalidates the roster AND the open project detail, not just the roster', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)

    invalidateBoardQueries('lido-audit')

    expect(spy.mock.calls.map(call => call[0]?.queryKey)).toEqual([
      PROJECTS_KEY,
      projectKey('lido-audit'),
      tasksKey('lido-audit'),
      stateKey('lido-audit')
    ])
  })

  it('invalidates the whole plugin cache when no slug is known', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined)

    invalidateBoardQueries()

    expect(spy.mock.calls.map(call => call[0]?.queryKey)).toEqual([['projects-board']])
  })
})
