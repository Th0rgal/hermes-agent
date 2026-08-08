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
