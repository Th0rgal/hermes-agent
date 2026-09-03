import assert from 'node:assert/strict'
import { test } from 'node:test'

import { versionsInLockstep } from './assert-version-lockstep.mjs'

test('matching versions pass', () => {
  const result = versionsInLockstep('{"version":"0.20.4"}', 'name = "hermes"\nversion = "0.20.4"\n')
  assert.equal(result.ok, true)
})

test('drift fails with both versions named', () => {
  const result = versionsInLockstep('{"version":"0.17.0"}', 'version = "0.20.4"\n')
  assert.deepEqual(result, { desktop: '0.17.0', hermes: '0.20.4', ok: false })
})

test('a missing pyproject version fails', () => {
  assert.equal(versionsInLockstep('{"version":"0.20.4"}', 'name = "hermes"\n').ok, false)
})
