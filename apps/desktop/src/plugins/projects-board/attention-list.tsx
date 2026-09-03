/**
 * Attention items with the one button that clears each — on the card (compact)
 * and in the drawer's "Needs you" section. Actions map by `kind` to the
 * plugin's relays; unknown kinds render read-only.
 */

import { Codicon, host, useMutation } from '@hermes/plugin-sdk'

import {
  acknowledgeMission,
  type AttentionItem,
  invalidateBoardQueries,
  projectAction,
  resumeMission
} from './api'
import { useBoard } from './i18n'

type Verb = 'acknowledge' | 'answer' | 'pause' | 'resume'

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** Which verbs clear an item of this kind. */
export function attentionVerbs(item: AttentionItem): Verb[] {
  switch (item.kind) {
    case 'mission_failed':
      return item.mission_id ? ['acknowledge', 'resume'] : []

    case 'mission_awaiting_user':
      return item.mission_id ? ['acknowledge'] : []

    case 'decision_pending':
      return ['answer']

    case 'no_controller':

    case 'tracker_stale':
      return ['pause']

    default:
      return []
  }
}

function ActionButton({
  item,
  onOpen,
  slug,
  verb
}: {
  item: AttentionItem
  onOpen?: (slug: string) => void
  slug: string
  verb: Verb
}) {
  const b = useBoard()

  const label = {
    acknowledge: b.attentionAcknowledge,
    answer: b.attentionAnswer,
    pause: b.attentionPause,
    resume: b.attentionResume
  }[verb]

  const mut = useMutation({
    mutationFn: async () => {
      switch (verb) {
        case 'acknowledge':
          return acknowledgeMission(item.mission_id!)

        case 'resume':
          return resumeMission(item.mission_id!)

        case 'pause':
          return projectAction(slug, 'pause')

        default:
          return undefined
      }
    },
    onError: error => host.notify({ kind: 'error', message: errorText(error) }),
    onSuccess: () => invalidateBoardQueries(slug)
  })

  return (
    <button
      className="inline-flex shrink-0 items-center gap-1 rounded border border-amber-400/40 px-1.5 py-px text-[0.5625rem] uppercase tracking-wide text-amber-500 transition-colors hover:bg-amber-400/15 disabled:opacity-50"
      disabled={mut.isPending}
      onClick={event => {
        event.stopPropagation()

        if (verb === 'answer') {
          onOpen?.(slug)

          return
        }

        mut.mutate()
      }}
      type="button"
    >
      {label}
    </button>
  )
}

export function AttentionList({
  compact = false,
  items,
  onOpen,
  slug
}: {
  compact?: boolean
  items: AttentionItem[]
  onOpen?: (slug: string) => void
  slug: string
}) {
  if (items.length === 0) {
    return null
  }

  return (
    <div className={compact ? 'flex flex-col gap-0.5' : 'flex flex-col gap-1.5'}>
      {items.map(item => (
        <div
          className={
            compact
              ? 'flex items-center gap-1.5 text-[0.625rem] leading-snug text-amber-500'
              : 'flex flex-col gap-0.5 rounded-md border border-amber-400/25 bg-(--ui-bg-quinary) px-2.5 py-1.5 text-[0.71rem]'
          }
          key={`${item.kind}|${item.mission_id ?? ''}|${item.message}`}
        >
          <div className="flex min-w-0 items-center gap-1.5">
            {!compact && <Codicon name="warning" size="0.75rem" />}
            <span className={compact ? 'min-w-0 flex-1 truncate' : 'min-w-0 flex-1 text-amber-500'} title={item.message}>
              {item.message}
            </span>
            {attentionVerbs(item).map(verb => (
              <ActionButton item={item} key={verb} onOpen={onOpen} slug={slug} verb={verb} />
            ))}
          </div>
          {!compact && item.evidence_headline && item.evidence_headline !== item.message && (
            <div className="line-clamp-2 text-(--ui-text-quaternary)">{item.evidence_headline}</div>
          )}
        </div>
      ))}
    </div>
  )
}
