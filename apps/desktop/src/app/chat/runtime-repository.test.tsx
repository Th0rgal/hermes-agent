import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '@/lib/chat-messages'
import { assistantTextPart, textPart } from '@/lib/chat-messages'

import { useRuntimeMessageRepository } from './runtime-repository'

const user = (id: string, text: string): ChatMessage => ({
  id,
  role: 'user',
  parts: [textPart(text)]
})

const assistant = (id: string, text: string): ChatMessage => ({
  id,
  role: 'assistant',
  parts: [assistantTextPart(text)]
})

describe('useRuntimeMessageRepository', () => {
  it('links a plain transcript with unique ids', () => {
    const { result } = renderHook(() =>
      useRuntimeMessageRepository([user('u1', 'hi'), assistant('a1', 'hello'), user('u2', 'more')])
    )

    expect(result.current.messages.map(m => m.message.id)).toEqual(['u1', 'a1', 'u2'])
    expect(result.current.headId).toBe('u2')
  })

  it('survives duplicate message ids instead of crashing the chat surface', () => {
    // Two live bubbles minted in the same millisecond used to share an id
    // (`assistant-<Date.now()>`), and MessageRepository.link then threw
    // "A message with the same id already exists in the parent tree" — the
    // error boundary killed the whole workspace pane, and Retry rebuilt the
    // same repository forever. The repository must render both messages.
    const { result } = renderHook(() =>
      useRuntimeMessageRepository([
        user('u1', 'hi'),
        assistant('assistant-1700000000000', 'canary callback'),
        assistant('assistant-1700000000000', 'lido synthesis')
      ])
    )

    const ids = result.current.messages.map(m => m.message.id)

    expect(ids).toHaveLength(3)
    expect(new Set(ids).size).toBe(3)
    expect(result.current.headId).toBe(ids[2])
  })
})
