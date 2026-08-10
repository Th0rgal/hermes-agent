/**
 * Small shared bits for the session-bound surfaces (sidebar rows + board
 * cards): the color picker row — writing the SAME override store (under the
 * durable lineage id) the core sidebar's Appearance submenu writes, so Pinned
 * rows, Projects rows, and board cards all repaint together — and the unread
 * count pill.
 */

import {
  $sessionColorOverrides,
  ColorSwatches,
  host,
  Input,
  PROFILE_SWATCHES,
  sessionDurableId,
  setSessionColorOverride,
  useMutation,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'

import { steerMission } from './api'
import { useBoard } from './i18n'

// Local copy of board.tsx's errText (importing it would cycle board ↔ here).
function detailText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const brace = raw.indexOf('{')

  if (brace !== -1) {
    try {
      return (JSON.parse(raw.slice(brace)) as { detail?: string }).detail ?? raw
    } catch {
      // Not JSON — fall through.
    }
  }

  return raw
}

/** The one Enter-to-steer input — shared by the drawer's mission rows and the
 *  board card's inline chip affordance, so steering feels identical. */
export function SteerInput({ missionId, onDone }: { missionId: string; onDone?: () => void }) {
  const b = useBoard()
  const [draft, setDraft] = useState('')

  const send = useMutation({
    mutationFn: () => steerMission(missionId, draft.trim()),
    onError: err => host.notify({ kind: 'error', message: detailText(err) }),
    onSuccess: () => {
      setDraft('')
      host.notify({ kind: 'info', message: b.sent })
      onDone?.()
    }
  })

  return (
    <div className="flex items-center gap-1.5">
      <Input
        autoFocus
        className="h-6 flex-1 text-[0.75rem]"
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && draft.trim() && !send.isPending) {
            send.mutate()
          }
        }}
        placeholder={b.steerPlaceholder}
        value={draft}
      />
      <button
        className="shrink-0 rounded bg-primary/80 px-2 py-1 text-[0.6875rem] text-primary-foreground disabled:opacity-40"
        disabled={send.isPending || draft.trim().length === 0}
        onClick={() => send.mutate()}
        type="button"
      >
        {b.steer}
      </button>
    </div>
  )
}

/** Unread count pill — hidden at 0, capped at "9+". */
export function UnreadBadge({ count }: { count: number }) {
  const b = useBoard()

  if (count <= 0) {
    return null
  }

  // Neutral tokens, NOT the theme accent: several theme presets use an
  // amber/orange accent, and an accent-tinted pill at this size reads as an
  // amber ATTENTION dot — a severity signal a count must never fake. The
  // count itself is the information; quiet chrome carries it (same treatment
  // as kanban's count chips).
  return (
    <span
      aria-label={b.unread(count)}
      className="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-(--ui-bg-quaternary) px-1 text-[0.5625rem] font-semibold tabular-nums text-(--ui-text-secondary)"
      title={b.unread(count)}
    >
      {count > 9 ? '9+' : count}
    </span>
  )
}

export function SessionColorSwatchesRow({ sessionId }: { sessionId: string }) {
  const b = useBoard()
  const overrides = useValue($sessionColorOverrides)
  const durableId = sessionDurableId(sessionId)

  return (
    <ColorSwatches
      clearIcon="circle-slash"
      clearLabel={b.noColor}
      onChange={color => setSessionColorOverride(durableId, color)}
      swatches={PROFILE_SWATCHES}
      value={overrides[durableId] ?? null}
    />
  )
}
