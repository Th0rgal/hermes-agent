import { useEffect, useState } from 'react'

import { Tip } from '@/components/ui/tooltip'
import { getSessionMissions, type SessionMission } from '@/hermes'
import { cn } from '@/lib/utils'

/** Statuses where nothing is moving without a person. */
const NEEDS_ATTENTION = new Set(['failed', 'blocked', 'not_feasible', 'interrupted'])
/** Statuses where work is genuinely in flight — narrower than "not finished".
 *  `awaiting_user` is parked, and counting it as running is how a stalled
 *  mission disguises itself as a busy one. */
const RUNNING = new Set(['pending', 'active', 'waiting_background'])

/**
 * Missions this conversation spawned.
 *
 * Fetched through the Hermes gateway rather than from sandboxed.sh directly,
 * so the desktop holds no backend credential. The gateway answers 503 when the
 * host has no sandboxed.sh and 502 when it is unreachable; both hide the tag
 * rather than rendering "0 missions", which would be a claim we cannot make.
 */
export function useSessionMissions(sessionId: string | null | undefined, profile?: null | string) {
  const [missions, setMissions] = useState<SessionMission[] | null>(null)

  useEffect(() => {
    if (!sessionId) {
      setMissions(null)

      return
    }

    let cancelled = false

    getSessionMissions(sessionId, profile)
      .then((response) => {
        if (!cancelled) {
          setMissions(response.missions)
        }
      })
      .catch(() => {
        // Unreachable or unconfigured: say nothing rather than something false.
        if (!cancelled) {
          setMissions(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [sessionId, profile])

  return missions
}

function summarize(mission: SessionMission): string {
  const name = mission.title?.trim() || mission.id.slice(0, 8)
  const scope = [mission.project, mission.track].filter(Boolean).join(' · ')

  return scope ? `${name} — ${mission.status} (${scope})` : `${name} — ${mission.status}`
}

/**
 * Compact mission count for the conversation titlebar.
 *
 * Renders nothing when there are no missions: an empty conversation should not
 * grow a permanent "0" that means the same as the tag being absent.
 */
export function MissionTag({
  className,
  missions
}: {
  className?: string
  missions: SessionMission[] | null
}) {
  if (!missions || missions.length === 0) {
    return null
  }

  const attention = missions.filter((m) => NEEDS_ATTENTION.has(m.status)).length
  const running = missions.filter((m) => RUNNING.has(m.status)).length
  const label = attention > 0 ? `${attention}!` : running > 0 ? `${running}▸` : String(missions.length)

  const detail = [
    `${missions.length} mission${missions.length === 1 ? '' : 's'} from this conversation`,
    ...missions.slice(0, 8).map(summarize),
    missions.length > 8 ? `…and ${missions.length - 8} more` : ''
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <Tip label={detail}>
      <span
        aria-label={detail}
        className={cn(
          'grid h-4 shrink-0 place-items-center rounded-[3px] px-1 text-[0.5rem] font-semibold uppercase leading-none',
          attention > 0 ? 'text-[var(--ui-danger,#e5484d)]' : 'text-[var(--ui-text-quaternary)]',
          className
        )}
        role="img"
        style={{ backgroundColor: 'var(--ui-bg-tertiary, rgba(127,127,127,0.14))' }}
      >
        {label}
      </span>
    </Tip>
  )
}
