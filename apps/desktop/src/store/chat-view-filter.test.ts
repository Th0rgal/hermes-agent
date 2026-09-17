import { beforeEach, describe, expect, it, vi } from 'vitest'

const loadStore = async () => {
  vi.resetModules()

  return import('./chat-view-filter')
}

describe('chat view filter preference', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('defaults to all and persists changes', async () => {
    const first = await loadStore()

    expect(first.$chatViewFilter.get()).toBe('all')

    first.setChatViewFilter('reports')

    expect(window.localStorage.getItem('hermes.desktop.chatViewFilter')).toBe('reports')
    expect((await loadStore()).$chatViewFilter.get()).toBe('reports')
  })

  it('falls back to all for an unknown stored value', async () => {
    window.localStorage.setItem('hermes.desktop.chatViewFilter', 'everything')

    expect((await loadStore()).$chatViewFilter.get()).toBe('all')
  })
})
