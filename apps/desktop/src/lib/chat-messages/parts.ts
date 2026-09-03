import { mediaDisplayLabel, mediaMarkdownHref } from '@/lib/media'

import type { ChatMessage, ChatMessagePart } from './types'

export function textPart(text: string, timestamp?: number): ChatMessagePart {
  return { type: 'text', text, ...(timestamp !== undefined ? { timestamp } : {}) }
}

export function reasoningPart(text: string, timestamp?: number): ChatMessagePart {
  return { type: 'reasoning', text, ...(timestamp !== undefined ? { timestamp } : {}) }
}

const MEDIA_LINE_RE = /(^|\n)[\t ]*[`"']?MEDIA:\s*(?<line>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|\S+)[`"']?[\t ]*(\n|$)/g

const MEDIA_TAG_RE = /[`"']?MEDIA:\s*(?<inline>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|\S+)[`"']?/g

function unquoteMediaPath(value: string): string {
  const trimmed = value.trim()
  const quote = trimmed[0]

  return quote && quote === trimmed.at(-1) && ['"', "'", '`'].includes(quote) ? trimmed.slice(1, -1) : trimmed
}

function mediaLink(value: string): string {
  const path = unquoteMediaPath(value)

  return `[${mediaDisplayLabel(path)}](${mediaMarkdownHref(path)})`
}

export function renderMediaTags(text: string): string {
  return text
    .replace(
      MEDIA_LINE_RE,
      (_match, lead: string, value: string, trailer: string) => `${lead}${mediaLink(value)}${trailer}`
    )
    .replace(MEDIA_TAG_RE, (_match, value: string) => mediaLink(value))
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
}

export function assistantTextPart(text: string, timestamp?: number): ChatMessagePart {
  return textPart(renderMediaTags(text), timestamp)
}

export function chatMessageText(message: ChatMessage): string {
  return message.parts
    .filter((part): part is Extract<ChatMessagePart, { type: 'text' }> => part.type === 'text')
    .map(part => part.text)
    .join('')
}

export interface UnspokenTurnSpeech {
  /** First unspoken assistant bubble — stable for the turn, the live speech session binds to it. */
  id: string
  /** Whether the newest assistant bubble is still streaming. */
  pending: boolean
  /** All unspoken assistant text in message order, bubbles joined on a blank line. */
  text: string
}

/**
 * Collect every unspoken assistant bubble after `lastSpokenId`, in order.
 *
 * A turn with tool calls produces several assistant bubbles — narration
 * ("Let me check…") sealed as interims, then the final answer as a fresh
 * bubble. Voice conversation speaks a turn through ONE live session bound to
 * one response id, so it needs all of that text as a single growing string;
 * selecting only one bubble silently drops everything after it. The blank-line
 * join is a sentence boundary for the server's cutter, so a sealed bubble's
 * tail is flushed as soon as the next bubble starts.
 */
export function collectUnspokenTurnSpeech(
  messages: ChatMessage[],
  lastSpokenId: string | null
): UnspokenTurnSpeech | null {
  const spokenIndex = lastSpokenId ? messages.findLastIndex(m => m.id === lastSpokenId) : -1

  let id: string | null = null
  let pending = false
  const parts: string[] = []

  for (const message of messages.slice(spokenIndex + 1)) {
    if (message.role !== 'assistant' || message.hidden) {
      continue
    }

    pending = Boolean(message.pending)
    const text = chatMessageText(message).trim()

    if (!text) {
      continue
    }

    id ??= message.id
    parts.push(text)
  }

  if (!id) {
    return null
  }

  return { id, pending, text: parts.join('\n\n') }
}

const normalizeWs = (value: string) => value.replace(/\s+/g, ' ').trim()

/** `[STATE_SIGNATURE: …]` and `[CTRL: …]` are routing/state tokens for the
 * ingestor, never for the reader: the first field routes, the rest describes
 * controller state. The scheduler strips them before delivery, but several
 * emission shapes defeated its matcher and reached the operator verbatim
 * (backticks, a bold label, a bracket inside the payload, an over-long
 * payload). Messages persisted while that was true still carry the markers, so
 * stripping here is what clears the transcripts that already exist — the server
 * fix only helps new deliveries.
 *
 * Greedy up to the LAST `]` on the line so a nested `[blocked]` is consumed
 * whole; lazy matching stops at the first `]` and strands the remainder. */
const STATE_SIGNATURE_RE = /[ \t]*(?:\*\*[^*\n]{0,64}?\*\*[ \t]*:?[ \t]*)?`?\[(?:STATE_SIGNATURE|CTRL):[^\n]{1,4096}\]`?/gi

/** Empty-tag improvisation: `[CTRL:]` then prose *outside* the brackets.
 *  The structured form is `[CTRL: project | mode=…]`; models sometimes emit
 *  `[CTRL:] #2332 repair active; …` which the bracket matcher cannot close. */
const EMPTY_CTRL_LINE_RE = /^[ \t]*\[CTRL:\][^\n]*$/gim

/** Remove the controller markers and the blank line(s) they leave behind. */
export function stripStateSignature<T extends string | null | undefined>(text: T): T {
  if (!text || !(text.includes('[STATE_SIGNATURE:') || text.includes('[CTRL:'))) {return text}

  return text
    .replace(STATE_SIGNATURE_RE, '')
    .replace(EMPTY_CTRL_LINE_RE, '')
    .replace(/\n[ \t]*\n[ \t]*\n+/g, '\n\n')
    .trim() as T
}

/**
 * Drop earlier text parts that a later text part repeats verbatim (after
 * whitespace normalization). Providers that continue a turn after a tool
 * call sometimes re-send the previous assistant text as the next message's
 * prefix (tool_calls row, then a stop row with identical prose) — the turn
 * merge then holds the same paragraph twice and everything in it renders
 * twice, most visibly ::preview frames. The LAST occurrence is the
 * authoritative one; keep it.
 */
export function dedupeRepeatedTextInParts(parts: ChatMessagePart[]): ChatMessagePart[] {
  const lastByText = new Map<string, number>()

  parts.forEach((part, index) => {
    if (part.type === 'text') {
      const key = normalizeWs(part.text)

      if (key) {
        lastByText.set(key, index)
      }
    }
  })

  const dropped = parts.filter((part, index) => {
    if (part.type !== 'text') {
      return true
    }

    const key = normalizeWs(part.text)

    return !key || lastByText.get(key) === index
  })

  return dropped.length === parts.length ? parts : dropped
}


/**
 * Collapse tool-call parts that share a `toolCallId`. Providers that echo a
 * whole assistant row after tools replay the same calls; unique-id rewriting
 * would then paint two "Ran …" groups (one still spinning). Prefer the copy
 * that already has a result.
 */
export function dedupeRepeatedToolCallsInParts(parts: ChatMessagePart[]): ChatMessagePart[] {
  const keep = new Map<string, number>()

  parts.forEach((part, index) => {
    if (part.type !== 'tool-call' || !part.toolCallId) {
      return
    }

    const previous = keep.get(part.toolCallId)

    if (previous === undefined) {
      keep.set(part.toolCallId, index)

      return
    }

    const previousPart = parts[previous]

    const previousHasResult =
      previousPart.type === 'tool-call' && 'result' in previousPart && previousPart.result != null

    const hasResult = 'result' in part && part.result != null

    if (hasResult && !previousHasResult) {
      keep.set(part.toolCallId, index)
    }
  })

  const dropped = parts.filter((part, index) => {
    if (part.type !== 'tool-call' || !part.toolCallId) {
      return true
    }

    return keep.get(part.toolCallId) === index
  })

  return dropped.length === parts.length ? parts : dropped
}

/**
 * Merge the final assistant text into a message's parts.
 *
 * - Removes all existing `text` parts (they were streamed deltas, now superseded
 *   by the authoritative final response).
 * - Keeps `reasoning` parts, but drops one that the final text fully covers
 *   (reasoning ⊆ final) — the final restates it. A short final ("Done.") must
 *   NOT swallow a longer reasoning block that merely starts with it (#61447).
 * - Keeps all other part types (tool-call, image, etc.).
 * - Appends the final text as a new text part.
 */
export function mergeFinalAssistantText(
  parts: ChatMessagePart[],
  finalText: string,
  fallbackTimestamp?: number
): ChatMessagePart[] {
  // Empty / whitespace-only completion is not authoritative — keep streamed
  // text, reasoning, and tool parts (#95514).
  if (!finalText.trim()) {
    return parts
  }

  finalText = stripStateSignature(finalText) ?? finalText
  const dedupeReference = normalizeWs(finalText)

  const streamedText = normalizeWs(
    parts
      .filter((part): part is Extract<ChatMessagePart, { type: 'text' }> => part.type === 'text')
      .map(part => part.text)
      .join('')
  )

  // An authoritative final that is exactly the concatenation of streamed text
  // confirms the content without erasing text↔reasoning activity boundaries.
  if (streamedText && streamedText === dedupeReference) {
    return parts
  }

  const previousText = parts.findLast(part => part.type === 'text')

  const kept = parts.filter(part => {
    if (part.type === 'text') {
      // Sealed text parts were already finalized into their own bubbles —
      // this filter only runs on the LAST streaming bubble, so there are no
      // sealed parts here. All text parts are streamed deltas that get
      // replaced by the authoritative final text.
      return false
    }

    if (part.type !== 'reasoning' || !dedupeReference) {
      return true
    }

    // Reasoning is a restatement only when the final FULLY covers it.
    // The reverse direction is not considered — a short final must not
    // swallow a longer reasoning block (#61447).
    const r = normalizeWs(part.text)

    return !(r && dedupeReference.startsWith(r))
  })

  if (!finalText) {
    return kept
  }

  const finalPart = assistantTextPart(finalText, previousText?.timestamp ?? fallbackTimestamp)

  if (previousText?.completedAt !== undefined) {
    finalPart.completedAt = previousText.completedAt
  }

  return [...kept, finalPart]
}

/** Seal every still-open visible activity when the assistant turn stops. */
export function completeOpenTimelineParts(parts: ChatMessagePart[], completedAt: number): ChatMessagePart[] {
  return parts.map(part =>
    part.timestamp !== undefined && part.completedAt === undefined
      ? ({ ...part, completedAt } as ChatMessagePart)
      : part
  )
}

// Coalesce only adjacent deltas of the same channel. Switching between text
// and reasoning is a real timeline boundary and must remain visible even when
// both channels arrive inside one batched renderer flush.
function appendStreamPart(
  parts: ChatMessagePart[],
  type: 'reasoning' | 'text',
  delta: string,
  timestamp?: number
): { index: number; parts: ChatMessagePart[] } {
  const next = [...parts]

  const tailIndex = next.length - 1
  const tail = next[tailIndex]

  if (tail?.type === type && tail.completedAt === undefined) {
    next[tailIndex] = { ...tail, text: `${tail.text}${delta}` } as ChatMessagePart

    return { index: tailIndex, parts: next }
  }

  if (
    timestamp !== undefined &&
    (tail?.type === 'text' || tail?.type === 'reasoning') &&
    tail.completedAt === undefined
  ) {
    next[tailIndex] = { ...tail, completedAt: timestamp } as ChatMessagePart
  }

  const STREAM_PART: Record<'reasoning' | 'text', (text: string, timestamp?: number) => ChatMessagePart> = {
    reasoning: reasoningPart,
    text: textPart
  }

  next.push(STREAM_PART[type](delta, timestamp))

  return { index: next.length - 1, parts: next }
}

export function appendReasoningPart(parts: ChatMessagePart[], delta: string, timestamp?: number): ChatMessagePart[] {
  return appendStreamPart(parts, 'reasoning', delta, timestamp).parts
}

export function appendAssistantTextPart(
  parts: ChatMessagePart[],
  delta: string,
  timestamp?: number
): ChatMessagePart[] {
  const { index, parts: next } = appendStreamPart(parts, 'text', delta, timestamp)
  const part = next[index]

  if (part?.type !== 'text') {
    return next
  }

  const mayContainMedia =
    delta.includes('MEDIA:') || delta.includes('DIA:') || delta.includes('EDIA:') || delta.includes('IA:')

  if (mayContainMedia || part.text.includes('MEDIA:')) {
    const rendered = renderMediaTags(part.text)

    if (rendered !== part.text) {
      next[index] = { ...part, text: rendered }
    }
  }

  return next
}
