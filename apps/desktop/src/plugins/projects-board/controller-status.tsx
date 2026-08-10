/**
 * Controller-status icon — a subtle 12px inline SVG that says WHO stopped a
 * project's controller (replacing the old amber attention dot, which lumped
 * every stop together):
 *
 *   ⏸ two bars   — the OPERATOR paused it via the board action
 *   ⭘ power/slash — the controller cut itself (mode paused/blocked), or its
 *                   updates went stale (silent budget-death looks identical)
 *
 * Active controllers render nothing. Muted UI-token colors, tooltip carries
 * the provenance (and the cause/blocker or silence duration when known).
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

export function ControllerStatusIcon({ project }: { project: ProjectRow }) {
  const b = useBoard()
  const stop = controllerStop(project)

  if (!stop) {
    return null
  }

  const label =
    stop.kind === 'operator-paused'
      ? b.pausedByYou
      : stop.kind === 'self-stopped'
        ? b.controllerStopped(stop.cause ?? '')
        : b.controllerSilent(relativeTime(stop.lastAt))

  return (
    <Tip label={label}>
      <span aria-label={label} className="grid shrink-0 place-items-center text-(--ui-text-tertiary)" role="img">
        {stop.kind === 'operator-paused' ? <PauseGlyph /> : <CutGlyph />}
      </span>
    </Tip>
  )
}
