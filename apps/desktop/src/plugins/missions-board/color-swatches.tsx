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
  PROFILE_SWATCHES,
  sessionDurableId,
  setSessionColorOverride,
  useValue
} from '@hermes/plugin-sdk'

import { useBoard } from './i18n'

/** Unread count pill — hidden at 0, capped at "9+". */
export function UnreadBadge({ count }: { count: number }) {
  const b = useBoard()

  if (count <= 0) {
    return null
  }

  return (
    <span
      aria-label={b.unread(count)}
      className="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full px-1 text-[0.5625rem] font-semibold tabular-nums"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--ui-accent) 18%, transparent)',
        color: 'var(--ui-accent)'
      }}
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
