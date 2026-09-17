/**
 * Controller-status icon — a subtle 12px inline SVG that says WHO/what stopped
 * a project's controller (replacing the old amber attention dot, which lumped
 * every stop together):
 *
 *   ⏸ two bars     — the OPERATOR paused it via the board action
 *   △ alert         — DEGRADED: the server says the controller is gone
 *                     (missing) or its output is not reaching a durable
 *                     conversation (dropped/misrouted) — the honest signal
 *                     that replaces a lying "active"
 *   ⭘ power/slash   — the controller cut itself (mode paused/blocked), its
 *                     updates went stale, or it attached but never engaged
 *
 * Active controllers whose output reaches the user render nothing. The degraded
 * glyph carries a danger tone; the rest are muted UI-token colors. Tooltip
 * carries the provenance (and the cause/blocker or silence duration when known).
 */

import { relativeTime, Tip } from '@hermes/plugin-sdk'

import { controllerStop, type ProjectRow } from './api'
import { useBoard } from './i18n'

function PauseGlyph() {
  return (
    <svg aria-hidden fill="currentColor" height="12" viewBox="0 0 12 12" width="12">
      <rect height="7" rx="0.75" width="2" x="3" y="2.5" />
      <rect height="7" rx="0.75" width="2" x="7" y="2.5" />
    </svg>
  )
}

function CutGlyph() {
  // A power symbol: the broken ring + stem reads as "switched off".
  return (
    <svg
      aria-hidden
      fill="none"
      height="12"
      stroke="currentColor"
      strokeLinecap="round"
      strokeWidth="1.4"
      viewBox="0 0 12 12"
      width="12"
    >
      <path d="M3.55 3.4a4.1 4.1 0 1 0 4.9 0" />
      <path d="M6 1.2v4.3" />
    </svg>
  )
}

function WarnGlyph() {
  // An alert triangle: the engine is running blind or gone — needs a look.
  return (
    <svg
      aria-hidden
      fill="none"
      height="12"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.3"
      viewBox="0 0 12 12"
      width="12"
    >
      <path d="M6 1.6 11 10.4H1z" />
      <path d="M6 4.8v2.4" />
      <path d="M6 8.9v.05" />
    </svg>
  )
}

export function ControllerStatusIcon({ project }: { project: ProjectRow }) {
  const b = useBoard()
  const stop = controllerStop(project)

  if (!stop) {
    return null
  }

  const label =
    stop.kind === 'operator-paused'
      ? b.pausedByYou
      : stop.kind === 'degraded'
        ? b.controllerDegraded(stop.reason)
        : stop.kind === 'self-stopped'
          ? b.controllerStopped(stop.cause ?? '')
          : stop.kind === 'waiting'
            ? b.controllerWaiting(stop.cause ?? '')
            : stop.kind === 'never-engaged'
              ? b.controllerNeverEngaged
              : b.controllerSilent(relativeTime(stop.lastAt))

  // Degraded is a real problem (engine gone / output lost) → danger tone; the
  // rest are quiet provenance markers.
  const tone =
    stop.kind === 'degraded' ? 'text-(--ui-text-danger)' : 'text-(--ui-text-tertiary)'

  const glyph =
    stop.kind === 'operator-paused' ? (
      <PauseGlyph />
    ) : stop.kind === 'degraded' ? (
      <WarnGlyph />
    ) : (
      <CutGlyph />
    )

  return (
    <Tip label={label}>
      <span aria-label={label} className={`grid shrink-0 place-items-center ${tone}`} role="img">
        {glyph}
      </span>
    </Tip>
  )
}
