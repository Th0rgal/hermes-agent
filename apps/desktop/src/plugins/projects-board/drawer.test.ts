import { describe, expect, it } from 'vitest'

import { parseMergeAuthority, serializeMergeAuthority } from './drawer'

// The backend grammar (projects_store.rs): `full | repo:a,b | review-first`.
// The UI must round-trip every stored shape, including legacy free text.
describe('merge authority grammar', () => {
  it('parses the documented shapes', () => {
    expect(parseMergeAuthority('full')).toEqual({ choice: 'full', detail: '' })
    expect(parseMergeAuthority('review-first')).toEqual({ choice: 'review-first', detail: '' })
    expect(parseMergeAuthority('repo:a/b,c/d')).toEqual({ choice: 'repos', detail: 'a/b,c/d' })
    expect(parseMergeAuthority('')).toEqual({ choice: '', detail: '' })
    expect(parseMergeAuthority(null)).toEqual({ choice: '', detail: '' })
  })

  it('preserves legacy free text as custom', () => {
    const parsed = parseMergeAuthority('merge only docs changes')

    expect(parsed).toEqual({ choice: 'custom', detail: 'merge only docs changes' })
    expect(serializeMergeAuthority(parsed.choice, parsed.detail)).toBe('merge only docs changes')
  })

  it('round-trips every documented shape', () => {
    for (const value of ['full', 'review-first', 'repo:a/b,c/d']) {
      const { choice, detail } = parseMergeAuthority(value)

      expect(serializeMergeAuthority(choice, detail)).toBe(value)
    }
  })

  it('normalizes repo lists and empties to null', () => {
    expect(serializeMergeAuthority('repos', ' a/b , c/d ,, ')).toBe('repo:a/b,c/d')
    expect(serializeMergeAuthority('repos', '  ')).toBeNull()
    expect(serializeMergeAuthority('custom', '  ')).toBeNull()
    expect(serializeMergeAuthority('', '')).toBeNull()
  })
})
