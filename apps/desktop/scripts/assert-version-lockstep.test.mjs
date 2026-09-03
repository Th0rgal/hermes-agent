import { describe, expect, it } from 'vitest'

import { versionsInLockstep } from './assert-version-lockstep.mjs'

describe('versionsInLockstep', () => {
  it('matching versions pass', () => {
    expect(versionsInLockstep('{"version":"0.20.4"}', 'name = "hermes"\nversion = "0.20.4"\n').ok).toBe(true)
  })

  it('drift fails with both versions named', () => {
    expect(versionsInLockstep('{"version":"0.17.0"}', 'version = "0.20.4"\n')).toEqual({
      desktop: '0.17.0',
      hermes: '0.20.4',
      ok: false
    })
  })

  it('a missing pyproject version fails', () => {
    expect(versionsInLockstep('{"version":"0.20.4"}', 'name = "hermes"\n').ok).toBe(false)
  })
})
