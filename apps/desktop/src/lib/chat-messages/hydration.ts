import { skillInvocationText } from '@hermes/shared'

import { extractImageRefs } from '@/lib/embedded-images'
import { dedupeGeneratedImageEchoesInParts } from '@/lib/generated-images'
import type { MessageReaction, SessionMessage } from '@/types/hermes'

import {
  assistantTextPart,
  chatMessageText,
  dedupeRepeatedTextInParts,
  dedupeRepeatedToolCallsInParts,
  reasoningPart,
  stripStateSignature,
  textPart
} from './parts'
import {
  applyStoredToolResult,
  applyStoredToolResultToParts,
  storedToolMessagePart,
  textFromUnknown,
  toolPartFromStoredCall,
  withUniqueToolCallIds
} from './tool-parts'
import type { ChatMessage, ChatMessagePart } from './types'

const ATTACHED_CONTEXT_MARKER_RE = /(?:^|\n)--- Attached Context ---\s*\n/
const CONTEXT_WARNINGS_MARKER_RE = /(?:^|\n)--- Context Warnings ---[\s\S]*$/
const CONTEXT_REF_RE = /@(file|folder|url|image|tool|terminal):(?:"[^"\n]+"|'[^'\n]+'|`[^`\n]+`|\S+)/g

/** Scheduler-written durable deliveries prefix their content with
 * "[Cron delivery: <job name>]\n". Paired with `observed` provenance the
 * sentinel identifies the row as a delivery — the UI lifts it into a divider
 * and shows only the payload. */
const CRON_DELIVERY_SENTINEL_RE = /^\s*\[Cron delivery:\s*([^\]]*)\]\s*/

/** The controller/routing trailer tokens a delivery carries (the same shapes
 *  `stripStateSignature` removes from the visible text). Collected verbatim
 *  so `deliveryNeedsOwner` can read their fields. */
const DELIVERY_TRAILER_RE = /\[(?:STATE_SIGNATURE|CTRL):[^\n]{1,4096}\]/gi
const DECISION_TRAILER_RE = /\[DECISION:/i
/** "Action Thomas : <value>" — the controller's owner-facing ask line. */
const ACTION_OWNER_LINE_RE = /^[ \t]*(?:\*\*)?Action Thomas\s*(?:\*\*)?\s*:\s*(.*)$/im
/** Values that mean "nothing to do" ("aucune pour l’instant." included). */
const NO_ACTION_VALUE_RE = /^(?:\*\*)?\s*(?:aucune|none|rien)\b/i
const BLOCKER_FIELD_RE = /\bblocker\s*=\s*([^|\]]*)/i
const NO_BLOCKER_VALUE_RE = /^(?:none|null|no|-|n\/a)?$/i

/** Whether a delivery is waiting on the owner and must not auto-collapse.
 *  `text` is the visible body (trailers already stripped); `trailer` is the
 *  raw `[CTRL: …]` / `[STATE_SIGNATURE: …]` token text. Pure. */
export function deliveryNeedsOwner(text: string, trailer: string): boolean {
  if (DECISION_TRAILER_RE.test(text)) {
    return true
  }

  const action = ACTION_OWNER_LINE_RE.exec(text)?.[1]?.trim() ?? ''

  if (action && !NO_ACTION_VALUE_RE.test(action)) {
    return true
  }

  const blocker = BLOCKER_FIELD_RE.exec(trailer)?.[1]?.trim() ?? ''

  return !NO_BLOCKER_VALUE_RE.test(blocker)
}

function deliveryTrailer(content: null | string | undefined): string {
  return content ? (content.match(DELIVERY_TRAILER_RE) ?? []).join(' ') : ''
}

function displayContentForMessage(role: SessionMessage['role'], content: unknown): string {
  const textContent = textFromUnknown(content)

  if (role !== 'user') {
    return textContent
  }

  // A `/skill` turn is stored expanded (the whole skill body). Current
  // gateways project it to the invocation before it ever reaches us; this is
  // the fallback for an older backend that still ships the raw payload.
  const invocation = skillInvocationText(textContent)

  if (invocation) {
    return invocation
  }

  const marker = textContent.match(ATTACHED_CONTEXT_MARKER_RE)

  if (!marker || marker.index === undefined) {
    return textContent.replace(CONTEXT_WARNINGS_MARKER_RE, '').trim()
  }

  const visibleText = textContent.slice(0, marker.index).replace(CONTEXT_WARNINGS_MARKER_RE, '').trim()
  const attachedContext = textContent.slice(marker.index + marker[0].length)
  const refs = [...new Set(Array.from(attachedContext.matchAll(CONTEXT_REF_RE)).map(match => match[0]))]

  // The prose keeps the `@file:` token the user typed, so it already chips in
  // place. Only hoist a ref the prose is missing — a turn persisted by an older
  // backend that stripped the tokens. Re-listing an inline ref would chip twice.
  const missing = refs.filter(ref => !visibleText.includes(ref))

  return [missing.join('\n'), visibleText].filter(Boolean).join('\n\n') || visibleText
}

function transcriptContent(displayKind: SessionMessage['display_kind'], content: string): string | null {
  return displayKind === 'hidden' || displayKind === 'intentional_silence' ? null : content
}

// A remote backend older than this app serves display_metadata as raw JSON text,
// and `in` throws on a primitive — which used to fail the whole session resume.
function parseDisplayMetadata(metadata: SessionMessage['display_metadata']): null | Record<string, unknown> {
  let parsed: unknown = metadata

  if (typeof parsed === 'string') {
    try {
      parsed = JSON.parse(parsed)
    } catch {
      return null
    }
  }

  return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
}

function timelineTaskCount(metadata: SessionMessage['display_metadata']): number | undefined {
  const count = parseDisplayMetadata(metadata)?.task_count

  return typeof count === 'number' ? count : undefined
}

function messageReactions(metadata: SessionMessage['display_metadata']): MessageReaction[] {
  const reactions = parseDisplayMetadata(metadata)?.reactions

  if (!Array.isArray(reactions)) {
    return []
  }

  return reactions.filter(
    (r): r is MessageReaction => Boolean(r) && typeof r === 'object' && typeof (r as MessageReaction).emoji === 'string'
  )
}

/** Rows the mission-callback route wrote before it typed them. Same three
 *  fixed prefixes as `tui_gateway/server.py::_legacy_display_kind`; this is
 *  the migration for transcripts already on disk, not how new rows get typed. */
const MISSION_CALLBACK_WAKE_PREFIX = 'A routed mission-complete callback'
const MISSION_CALLBACK_SEPARATOR_PREFIX = 'A mission you started has finished'
const MISSION_CALLBACK_PREFIX = '[Mission callback:'

export function legacyDisplayKind(role: SessionMessage['role'], text: string): SessionMessage['display_kind'] | undefined {
  const stripped = text.trimStart()

  if (role === 'user' && stripped.startsWith(MISSION_CALLBACK_WAKE_PREFIX)) {
    return 'mission_callback_wake'
  }

  if (role === 'user' && stripped.startsWith(MISSION_CALLBACK_SEPARATOR_PREFIX)) {
    return 'hidden'
  }

  if (role === 'assistant' && stripped.startsWith(MISSION_CALLBACK_PREFIX)) {
    return 'mission_callback'
  }

  return undefined
}

/** User rows that are events, not operator prompts. They render as timeline
 *  lines and must never take part in prompt de-duplication. */
export const SYSTEM_TYPED_USER_KINDS: ReadonlySet<string> = new Set([
  'model_switch',
  'async_delegation_complete',
  'auto_continue',
  'personality_switch',
  'mission_callback_wake'
])

function missionCallbackFacts(metadata: SessionMessage['display_metadata'], content: string): { status: string; title: string } {
  const parsed = parseDisplayMetadata(metadata)
  const metaTitle = typeof parsed?.title === 'string' ? parsed.title.trim() : ''
  const metaStatus = typeof parsed?.status === 'string' ? parsed.status.trim() : ''
  // Legacy rows carry the facts in the prose: "[Mission callback: <title>]\nstatus=<s> mission=…".
  const headerTitle = /^\s*\[Mission callback:\s*([^\]]*)\]/.exec(content)?.[1]?.trim() ?? ''
  const proseStatus = /^status=(\S+)/m.exec(content)?.[1]?.trim() ?? ''

  return { status: metaStatus || proseStatus, title: metaTitle || headerTitle }
}

/** "mission finished · <title> · <status>" — the divider / timeline label. */
export function missionCallbackLabel(metadata: SessionMessage['display_metadata'], content: string): string {
  const { status, title } = missionCallbackFacts(metadata, content)

  return ['mission finished', title, status].filter(Boolean).join(' · ')
}

/** The callback prose without its machine header line — the divider carries it. */
function missionCallbackBody(content: string): string {
  return content.replace(/^\s*\[Mission callback:[^\n]*\n?/, '').replace(/^status=[^\n]*\n?/, '')
}

const INTENTIONAL_SILENCE_MARKERS = new Set(['[SILENT]', 'SILENT', 'NO_REPLY', 'NO REPLY'])

function withoutIntentionalSilenceTurns(messages: SessionMessage[]): SessionMessage[] {
  const hidden = new Set<number>()
  let turnStart: null | number = null

  messages.forEach((message, index) => {
    if (message.role === 'user') {
      turnStart = index
    }

    if (message.display_kind === 'intentional_silence') {
      hidden.add(index)
    }

    const content = textFromUnknown(message.content || message.text || message.context || message.name)

    if (message.role === 'assistant' && INTENTIONAL_SILENCE_MARKERS.has(content.trim().toUpperCase())) {
      const start = turnStart ?? index
      const trigger = messages[start]
      const triggerContent = textFromUnknown(trigger.content || trigger.text || trigger.context || trigger.name)

      if (triggerContent.toLowerCase().includes('sandboxed.sh mission changed status')) {
        for (let turnIndex = start; turnIndex <= index; turnIndex += 1) {
          hidden.add(turnIndex)
        }
      }
    }
  })

  return messages.filter((_message, index) => !hidden.has(index))
}

function timelineDisplayContent(message: SessionMessage, content: string): string {
  if (message.display_kind === 'model_switch') {
    return 'model changed'
  }

  if (message.display_kind === 'auto_continue') {
    return 'resumed interrupted turn'
  }

  if (message.display_kind === 'personality_switch') {
    return 'personality changed'
  }

  if (message.display_kind === 'async_delegation_complete') {
    const count = timelineTaskCount(message.display_metadata)

    return count === undefined
      ? 'background agent work finished'
      : `${count} background agent${count === 1 ? '' : 's'} finished`
  }

  if (message.display_kind === 'mission_callback_wake') {
    // Never the wake prompt itself — the operator did not type it.
    return missionCallbackLabel(message.display_metadata, '')
  }

  return content
}

export function toChatMessages(messages: SessionMessage[]): ChatMessage[] {
  const visibleMessages = withoutIntentionalSilenceTurns(messages)
  const result: ChatMessage[] = []
  let pendingToolParts: ChatMessagePart[] = []
  let pendingToolTimestamp: number | undefined
  let activeAssistantIndex: null | number = null

  const clearPendingTools = () => {
    pendingToolParts = []
    pendingToolTimestamp = undefined
  }

  const earliestTimestamp = (...values: (number | undefined)[]) => {
    const timestamps = values.filter((value): value is number => value !== undefined)

    return timestamps.length ? Math.min(...timestamps) : undefined
  }

  const appendPartsToActiveAssistant = (parts: ChatMessagePart[], timestamp?: number): boolean => {
    if (activeAssistantIndex === null) {
      return false
    }

    const active = result[activeAssistantIndex]

    if (!active || active.role !== 'assistant') {
      activeAssistantIndex = null

      return false
    }

    active.parts = [...active.parts, ...parts]
    active.timestamp = earliestTimestamp(active.timestamp, timestamp, ...parts.map(part => part.timestamp))

    return true
  }

  const flushPendingTools = (index: number) => {
    if (!pendingToolParts.length) {
      return
    }

    if (!appendPartsToActiveAssistant(pendingToolParts, pendingToolTimestamp)) {
      result.push({
        id: `${pendingToolTimestamp || Date.now()}-${index}-tools`,
        role: 'assistant',
        parts: pendingToolParts,
        timestamp: pendingToolTimestamp
      })
      activeAssistantIndex = result.length - 1
    }

    clearPendingTools()
  }

  visibleMessages.forEach((message, index) => {
    if (message.role === 'tool') {
      const updatedPendingToolParts = applyStoredToolResultToParts(pendingToolParts, message)

      if (updatedPendingToolParts) {
        pendingToolParts = updatedPendingToolParts

        return
      }

      if (applyStoredToolResult(result, message)) {
        return
      }

      pendingToolParts = [...pendingToolParts, storedToolMessagePart(message, index)]
      pendingToolTimestamp ??= message.timestamp

      return
    }

    const content =
      message.display_content !== undefined
        ? message.display_content
        : message.content || message.text || message.context || message.name

    const contentText = textFromUnknown(content)

    // Preserve compatibility with cron rows written before delivery switched
    // to the assistant role. Requiring both provenance and the scheduler
    // sentinel prevents a human-authored lookalike from spoofing agent output.
    const isObserved = message.observed === true || message.observed === 1

    const deliveryMatch = isObserved ? CRON_DELIVERY_SENTINEL_RE.exec(contentText) : null

    const isObservedCronDelivery = message.role === 'user' && deliveryMatch !== null

    // Untyped legacy mission-callback rows get their kind from the fixed prefix.
    const displayKind = message.display_kind ?? legacyDisplayKind(message.role, contentText)
    const typedMessage: SessionMessage = displayKind === message.display_kind ? message : { ...message, display_kind: displayKind }

    const durableDisplayRole: SessionMessage['role'] =
      displayKind !== undefined && SYSTEM_TYPED_USER_KINDS.has(displayKind) ? 'system' : message.role

    const displayRole: SessionMessage['role'] = isObservedCronDelivery ? 'assistant' : durableDisplayRole

    const isMissionCallback = displayKind === 'mission_callback' && message.role === 'assistant'

    const deliveryBase: Pick<NonNullable<ChatMessage['delivery']>, 'kind' | 'label'> | undefined = isMissionCallback
      ? { kind: 'mission_callback', label: missionCallbackLabel(message.display_metadata, contentText) }
      : deliveryMatch && displayRole === 'assistant'
        ? { kind: 'cron', label: deliveryMatch[1].trim() || 'cron' }
        : undefined

    const rawDisplayContent = transcriptContent(
      displayKind,
      timelineDisplayContent(typedMessage, displayContentForMessage(message.role, content))
    )

    // The sentinel is provenance, not prose — the divider carries the label.
    const sentinelStrippedContent = stripStateSignature(
      deliveryBase && rawDisplayContent
        ? isMissionCallback
          ? missionCallbackBody(rawDisplayContent)
          : rawDisplayContent.replace(CRON_DELIVERY_SENTINEL_RE, '')
        : rawDisplayContent
    )

    const delivery: ChatMessage['delivery'] = deliveryBase
      ? {
          ...deliveryBase,
          needsOwner: deliveryNeedsOwner(sentinelStrippedContent ?? '', deliveryTrailer(rawDisplayContent))
        }
      : undefined

    // Persisted user turns carry `@image:<path>` directive lines inline in
    // the text (see tui_gateway/server.py's persist-time rewrite). The
    // read-only bubble clamps its body to ~2 lines, and a large inline image
    // thumbnail pushes any caption text below the clamp's visible area — so
    // pull image refs out into `attachmentRefs` (same shape the local
    // optimistic composer already uses) and render them via the dedicated
    // attachments row below the bubble instead.
    const imageRefExtraction = displayRole === 'user' && sentinelStrippedContent ? extractImageRefs(sentinelStrippedContent) : null
    const displayContent = imageRefExtraction ? imageRefExtraction.cleanedText : sentinelStrippedContent
    const extractedAttachmentRefs = imageRefExtraction?.refs.length ? imageRefExtraction.refs : undefined

    const parts: ChatMessagePart[] = []

    const reasoning =
      message.reasoning ||
      message.reasoning_content ||
      (typeof message.reasoning_details === 'string' ? message.reasoning_details : '')

    if (reasoning && message.role === 'assistant') {
      parts.push(reasoningPart(reasoning, message.timestamp))
    }

    if (displayContent) {
      parts.push(
        displayRole === 'assistant'
          ? assistantTextPart(displayContent, message.timestamp)
          : textPart(displayContent, message.timestamp)
      )
    }

    if (message.role === 'assistant' && Array.isArray(message.tool_calls)) {
      parts.push(
        ...message.tool_calls.map((call, callIndex) => toolPartFromStoredCall(call, callIndex, message.timestamp))
      )
    }

    if (!parts.length && !extractedAttachmentRefs?.length) {
      if (message.role !== 'assistant') {
        flushPendingTools(index)
        activeAssistantIndex = null
      }

      return
    }

    const isToolOnlyAssistant =
      message.role === 'assistant' && parts.length > 0 && parts.every(part => part.type === 'tool-call')

    if (isToolOnlyAssistant) {
      pendingToolParts = [...pendingToolParts, ...parts]
      pendingToolTimestamp ??= message.timestamp

      return
    }

    if (message.role === 'assistant') {
      if (pendingToolParts.length) {
        if (!appendPartsToActiveAssistant(pendingToolParts, message.timestamp ?? pendingToolTimestamp)) {
          parts.unshift(...pendingToolParts)
        }

        clearPendingTools()
      }

      const activeAssistant =
        activeAssistantIndex !== null && result[activeAssistantIndex]?.role === 'assistant'
          ? result[activeAssistantIndex]
          : null

      const currentHasToolCall = parts.some(part => part.type === 'tool-call')
      const activeHasToolCall = Boolean(activeAssistant?.parts.some(part => part.type === 'tool-call'))

      // Deliveries are out-of-band drops: never fold one into the turn that
      // happens to precede it.
      if (activeAssistant && !delivery && (currentHasToolCall || activeHasToolCall)) {
        activeAssistant.parts = [...activeAssistant.parts, ...parts]
        activeAssistant.timestamp = earliestTimestamp(
          activeAssistant.timestamp,
          message.timestamp,
          ...parts.map(part => part.timestamp)
        )

        return
      }
    } else {
      flushPendingTools(index)
    }

    const reactions = messageReactions(message.display_metadata)
    // Gateway resume names the durable row id `row_id`; the REST transcript
    // prefetch ships the same messages.id as a numeric `id`. Either one lets
    // reactions address this exact row later.
    const rowId = message.row_id ?? (typeof message.id === 'number' ? message.id : undefined)

    result.push({
      id: `${message.timestamp || Date.now()}-${index}-${displayRole}`,
      role: displayRole,
      parts,
      timestamp: earliestTimestamp(message.timestamp, ...parts.map(part => part.timestamp)),
      ...(rowId !== undefined ? { rowId } : {}),
      ...(reactions.length ? { reactions } : {}),
      ...(extractedAttachmentRefs ? { attachmentRefs: extractedAttachmentRefs } : {}),
      ...(delivery ? { delivery } : {})
    })

    // A delivery bubble is closed on arrival — later rows must not merge in.
    activeAssistantIndex = displayRole === 'assistant' && !delivery ? result.length - 1 : null
  })
  flushPendingTools(visibleMessages.length)

  const withoutGeneratedImageEchoes = result.map(message =>
    message.role === 'assistant'
      ? {
          ...message,
          parts: dedupeRepeatedToolCallsInParts(
            dedupeRepeatedTextInParts(dedupeGeneratedImageEchoesInParts(message.parts))
          )
        }
      : message
  )

  return withUniqueToolCallIds(
    withoutGeneratedImageEchoes.filter(
      m => chatMessageText(m).trim() || m.parts.some(part => part.type !== 'text') || m.attachmentRefs?.length
    )
  )
}
