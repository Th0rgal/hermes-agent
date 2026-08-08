import { describe, expect, it } from 'vitest'

import { migrateRenamedDecisions } from './plugins-store'

describe('migrateRenamedDecisions', () => {
  it('carries an old-id decision onto the renamed id', () => {
    expect(migrateRenamedDecisions({ 'missions-board': true })).toEqual({
      'missions-board': true,
      'projects-board': true
    })
    expect(migrateRenamedDecisions({ 'missions-board': false })['projects-board']).toBe(false)
  })

  it('never overrides an explicit decision on the new id', () => {
    expect(migrateRenamedDecisions({ 'missions-board': true, 'projects-board': false })).toEqual({
      'missions-board': true,
      'projects-board': false
    })
  })

  it('is identity (same reference) when nothing needs migrating', () => {
    const decisions = { kanban: true }

    expect(migrateRenamedDecisions(decisions)).toBe(decisions)
  })
})

describe('decisions survive the rename (regression)', () => {
  it('kanban stays enabled when missions-board migrates', () => {
    const migrated = migrateRenamedDecisions({ kanban: true, 'missions-board': true })

    expect(migrated.kanban).toBe(true)
    expect(migrated['projects-board']).toBe(true)
    // Nothing is dropped — every stored decision is still present.
    expect(Object.keys(migrated).sort()).toEqual(['kanban', 'missions-board', 'projects-board'])
  })
})

describe('saveDecisions merges over the stored map', () => {
  it('a stale window toggling one plugin never clobbers decisions it has not seen', async () => {
    const { $pluginDecisions, setPluginEnabled } = await import('./plugins-store')

    window.localStorage.setItem('hermes.desktop.pluginDecisions.v2', JSON.stringify({ kanban: true }))
    // Simulate a window whose in-memory map predates the kanban decision.
    $pluginDecisions.set({})

    await setPluginEnabled('projects-board', true)

    const stored = JSON.parse(window.localStorage.getItem('hermes.desktop.pluginDecisions.v2')!) as Record<
      string,
      boolean
    >

    expect(stored).toEqual({ kanban: true, 'projects-board': true })
    expect($pluginDecisions.get().kanban).toBe(true)

    window.localStorage.removeItem('hermes.desktop.pluginDecisions.v2')
  })
})
