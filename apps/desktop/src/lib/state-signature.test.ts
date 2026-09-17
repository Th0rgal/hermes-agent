import { describe, expect, it } from 'vitest'

import { stripStateSignature } from './chat-messages'

/**
 * `[STATE_SIGNATURE: …]` routes and describes state for the ingestor. It is not
 * for the reader, and the operator reported seeing it at the end of messages in
 * the desktop app on 2026-08-05.
 *
 * The scheduler strips it before delivery, but four emission shapes defeated
 * its matcher. Messages persisted while that was true still carry the marker,
 * so this strip is what clears the transcripts that already exist.
 */
describe('stripStateSignature', () => {
  it('removes a bare marker', () => {
    expect(stripStateSignature('Done.\n[STATE_SIGNATURE: repo|pr7|abc|green]')).toBe('Done.')
  })

  it('removes a [CTRL: …] controller trailer', () => {
    expect(
      stripStateSignature('SCANNER DEAD\n[CTRL: coldcard-rng-cracker | mode=blocked | wait=1 | next=monitor]')
    ).toBe('SCANNER DEAD')
  })

  it('removes both CTRL and STATE_SIGNATURE trailers together', () => {
    const text =
      'SCANNER DEAD - checkpoint=2307719168\n[CTRL: coldcard-rng-cracker | mode=blocked] [STATE_SIGNATURE: coldcard-rng-cracker|scan|2307719168|scanner-dead|monitor-only]'

    expect(stripStateSignature(text)).toBe('SCANNER DEAD - checkpoint=2307719168')
  })

  it('removes one wrapped in backticks', () => {
    const text = 'Done.\n`[STATE_SIGNATURE: verity|phase1k|#2231|CONFLICTING]`'
    expect(stripStateSignature(text)).toBe('Done.')
  })

  it('removes one introduced by a bold label', () => {
    const text = 'Tick.\n\n**Status signature:** `[STATE_SIGNATURE: verity|phase1k|#2231]`'
    const out = stripStateSignature(text)
    expect(out).toBe('Tick.')
    expect(out).not.toContain('Status signature')
  })

  it('consumes a bracket nested in the payload', () => {
    // The exact line the operator reported.
    const text =
      'All lanes merged.\n[STATE_SIGNATURE: verity-program|none|066f1bf5|no-open-prs|none|next-tick [blocked]]'

    const out = stripStateSignature(text)
    expect(out).toBe('All lanes merged.')
    expect(out).not.toContain(']')
  })

  it('leaves no gap where the marker was', () => {
    expect(stripStateSignature('Before.\n\n[STATE_SIGNATURE: a|b]\n\nAfter.')).toBe(
      'Before.\n\nAfter.',
    )
  })

  it('leaves prose that merely mentions the word', () => {
    // Only the bracketed token is a token; talking about it is prose.
    const text = 'I emit a STATE_SIGNATURE line at the end of each tick.'
    expect(stripStateSignature(text)).toBe(text)
  })

  it('leaves ordinary text untouched and identical', () => {
    const text = 'Nothing machine-readable here.'
    expect(stripStateSignature(text)).toBe(text)
  })

  it('tolerates empty and undefined input', () => {
    expect(stripStateSignature('')).toBe('')
    expect(stripStateSignature(undefined)).toBeUndefined()
  })

  it('removes several markers in one message', () => {
    const text = 'A.\n[STATE_SIGNATURE: a|1]\nB.\n[STATE_SIGNATURE: b|2]'
    const out = stripStateSignature(text)
    expect(out).not.toContain('STATE_SIGNATURE')
    expect(out).toContain('A.')
    expect(out).toContain('B.')
  })

  it('removes an empty-tag [CTRL:] line with prose outside the brackets', () => {
    // The exact Verity leak: the model put the status *after* `[CTRL:]`
    // instead of inside `[CTRL: project | mode=…]`. The closer is immediate,
    // so the bracket matcher cannot consume the rest of the line.
    const text =
      '**Verity #2332 — réparation en cours, pas mergeable.**\n\n' +
      'Action Thomas : aucune pour l’instant.\n\n' +
      '[CTRL:] #2332 repair active; both workers recovered after server restart; waiting for successor head or concrete blocker.\n\n' +
      '[STATE_SIGNATURE: verity|pr2332|none|repair-active|source|successor-head-or-blocker]'

    const out = stripStateSignature(text)

    expect(out).toBe('**Verity #2332 — réparation en cours, pas mergeable.**\n\nAction Thomas : aucune pour l’instant.')
    expect(out).not.toContain('[CTRL:]')
    expect(out).not.toContain('workers recovered')
    expect(out).not.toContain('STATE_SIGNATURE')
  })
})
