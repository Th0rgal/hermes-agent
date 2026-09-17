import { describe, expect, it } from 'vitest'

import {
  classifyWorkspacePath,
  describeSessionLocation,
  localRegistryConnection,
  machineLabel
} from './session-location'

describe('classifyWorkspacePath', () => {
  it('treats a host checkout as host', () => {
    expect(classifyWorkspacePath('/Users/thomas/work/paloma')).toBe('host')
    expect(classifyWorkspacePath('/home/thomas/src/verity')).toBe('host')
  })

  it('flags sandboxed/nspawn mission trees as container', () => {
    expect(classifyWorkspacePath('/workspaces/mission-bcc18b82-a93b-4d5f-8482')).toBe('container')
    expect(classifyWorkspacePath('/workspace/lido-srv3-proof-closure')).toBe('container')
    expect(classifyWorkspacePath('/var/lib/machines/mission-foo/root')).toBe('container')
    expect(classifyWorkspacePath('/root/.sandboxed-sh/missions/abc')).toBe('container')
  })

  it('returns unknown for empty cwd', () => {
    expect(classifyWorkspacePath('')).toBe('unknown')
    expect(classifyWorkspacePath(null)).toBe('unknown')
  })
})

describe('machineLabel', () => {
  it('names the local runtime This Mac', () => {
    expect(machineLabel({ mode: 'local', baseUrl: 'http://127.0.0.1:8642' })).toBe('This Mac')
    expect(machineLabel(null)).toBe('This Mac')
  })

  it('uses the SSH/remote host when connected remotely', () => {
    expect(
      machineLabel({ mode: 'remote', remoteKind: 'ssh', remoteHost: 'agent-core', baseUrl: 'http://127.0.0.1:8642' })
    ).toBe('agent-core')
    expect(machineLabel({ mode: 'remote', remoteKind: 'url', baseUrl: 'https://agent-backend.thomas.md/hermes-desktop' })).toBe(
      'agent-backend.thomas.md'
    )
  })
})

describe('describeSessionLocation', () => {
  it('joins machine and workspace leaf', () => {
    const location = describeSessionLocation({
      connection: { mode: 'local', baseUrl: 'http://127.0.0.1:8642' },
      cwd: '/Users/thomas/work/paloma/sandboxed_sh'
    })

    expect(location.label).toBe('This Mac · sandboxed_sh')
    expect(location.native).toBe(true)
    expect(location.virtualized).toBe(false)
  })

  it('marks a remote container workspace as virtualized', () => {
    const location = describeSessionLocation({
      connection: { mode: 'remote', remoteKind: 'ssh', remoteHost: 'agent-core', baseUrl: 'http://127.0.0.1:8642' },
      cwd: '/workspaces/mission-abc'
    })

    expect(location.label).toBe('agent-core · mission-abc')
    expect(location.virtualized).toBe(true)
    expect(location.kind).toBe('container')
  })
})

describe('localRegistryConnection', () => {
  it('picks the native local registry entry', () => {
    const local = localRegistryConnection([
      { id: 'ssh-1', kind: 'ssh', label: 'agent-core', tokenSet: false, tokenPreview: null },
      { id: 'local', kind: 'local', label: 'This computer', tokenSet: false, tokenPreview: null }
    ])

    expect(local?.id).toBe('local')
  })
})
