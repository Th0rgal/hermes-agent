/**
 * Plugin-scoped i18n for projects-board — bundles shipped under the plugin id
 * via ctx.i18n.register, never touching core en.ts. `useBoard()` binds the
 * translator to the message SHAPE so components keep typed access.
 */

import { type PluginLocaleBundles, type PluginTranslate, usePluginI18n } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

type BoardMessages = {
  title: string
  nav: string
  sidebarSection: string
  open: string
  empty: string
  unreachable: string
  loading: string
  noProjects: string
  countTip: (active: number, attention: number) => string
  liveCount: (n: number) => string
  needsYou: (n: number) => string
  col: Record<
    'archived' | 'active' | 'attention' | 'paused',
    { help: string; label: string }
  >
  attentionLocked: string
  moveTo: (label: string) => string
  openProject: string
  expand: (label: string) => string
  collapse: (label: string) => string
  colEmpty: string
  introBody: string
  introGotIt: string
  updates: (n: number) => string
  missions: (n: number) => string
  live: (n: number) => string
  moreChips: (n: number) => string
  failed: (n: number) => string
  overdue: (n: number) => string
  tracksAttention: (n: number) => string
  steerPlaceholder: string
  steer: string
  sent: string
  // drawer
  objective: string
  nextAction: string
  blocker: string
  controllerCron: string
  repository: string
  mode: string
  status: string
  waitTicks: string
  tracks: string
  grant: string
  grantHint: string
  mergeAuthority: string
  budgetPerTick: string
  parallelMissions: string
  pauseReason: string
  resumeCondition: string
  materialBar: string
  saveGrant: string
  grantSaved: string
  openDecisions: string
  stateTimeline: string
  observations: (n: number) => string
  conversation: string
  openConversation: string
  unread: (n: number) => string
  menuOptions: string
  nextActionArrow: (action: string) => string
  focusAttentionTip: string
  paletteOpenConversation: (slug: string) => string
  paletteOpenCard: (slug: string) => string
  steerMissionTip: string
  attentionNotifTitle: string
  attentionNotifBody: (slug: string) => string
  openBoardAction: string
  notifyToggleOn: string
  notifyToggleOff: string
  setColor: string
  noColor: string
  openCard: string
  pauseProject: string
  resumeProject: string
  deleteProject: string
  deleteConfirm: string
  deleteProjectTitle: (name: string) => string
  deleteProjectBody: string
  deleteProjectDone: (name: string) => string
  renameProject: string
  archiveProject: string
  renameConfirm: string
  renameProjectTitle: (name: string) => string
  renamePlaceholder: string
  liveMissions: string
  noMissions: string
  close: string
  needsYouTag: string
  pausedByYou: string
  controllerStopped: (cause: string) => string
  controllerSilent: (since: string) => string
}

const en: BoardMessages = {
  title: 'Projects',
  nav: 'Projects',
  sidebarSection: 'Projects',
  open: 'Projects: Open',
  empty: 'No sandboxed.sh projects on this host.',
  unreachable: 'sandboxed.sh is unreachable.',
  loading: 'Loading projects…',
  noProjects: 'No projects yet — the controller roster is empty.',
  countTip: (active, attention) =>
    attention > 0 ? `${active} missions running · ${attention} need you` : `${active} missions running`,
  liveCount: n => `${n} mission${n === 1 ? '' : 's'} running`,
  needsYou: n => `${n} need${n === 1 ? 's' : ''} you`,
  col: {
    attention: { help: 'Projects that need the operator — blocked, failing, or overdue.', label: 'Needs attention' },
    active: { help: 'Controllers working autonomously.', label: 'Active' },
    paused: { help: 'Deliberately parked — drag back to Active to resume.', label: 'Paused' },
    archived: { help: 'Retired projects, kept for the record.', label: 'Archived' }
  },
  attentionLocked: 'Attention is computed from project health — resolve the reasons, don’t drag them away.',
  moveTo: label => `Move to ${label}`,
  openProject: 'Open',
  expand: label => `Expand ${label}`,
  collapse: label => `Collapse ${label}`,
  colEmpty: 'Empty',
  introBody:
    'A controller drives a project through its control conversation by dispatching missions. Attention is computed from health — resolve it, don’t drag it away.',
  introGotIt: 'Got it',
  updates: n => `${n} update${n === 1 ? '' : 's'}`,
  missions: n => `${n} mission${n === 1 ? '' : 's'}`,
  live: n => `${n} live`,
  moreChips: n => `+${n}`,
  failed: n => `${n} failed`,
  overdue: n => `${n} overdue`,
  tracksAttention: n => `${n} track${n === 1 ? '' : 's'} need attention`,
  steerPlaceholder: 'Nudge this mission…',
  steer: 'Send',
  sent: 'Message delivered to the mission.',
  objective: 'Objective',
  nextAction: 'Next action',
  blocker: 'Blocker',
  controllerCron: 'Controller cron',
  repository: 'Repository',
  mode: 'Mode',
  status: 'Status',
  waitTicks: 'Wait ticks',
  tracks: 'Tracks',
  grant: 'Autonomy grant',
  grantHint: 'What the controller may do without asking.',
  mergeAuthority: 'Merge authority',
  budgetPerTick: 'Budget per tick',
  parallelMissions: 'Parallel missions',
  pauseReason: 'Pause reason',
  resumeCondition: 'Resume condition',
  materialBar: 'Material bar',
  saveGrant: 'Save grant',
  grantSaved: 'Grant updated.',
  openDecisions: 'Open decisions',
  stateTimeline: 'State timeline',
  observations: n => `${n} observation${n === 1 ? '' : 's'}`,
  conversation: 'Conversation',
  openConversation: 'Open conversation',
  unread: n => `${n} unread message${n === 1 ? '' : 's'}`,
  menuOptions: 'Project options',
  nextActionArrow: action => `→ ${action}`,
  focusAttentionTip: 'Show projects needing attention',
  paletteOpenConversation: slug => `Project: ${slug} — open conversation`,
  paletteOpenCard: slug => `Project: ${slug} — open board card`,
  steerMissionTip: 'Steer this mission',
  attentionNotifTitle: 'Project needs attention',
  attentionNotifBody: slug => `${slug} needs your attention`,
  openBoardAction: 'Open board',
  notifyToggleOn: 'Attention notifications on — click to mute',
  notifyToggleOff: 'Attention notifications off — click to enable',
  setColor: 'Set color',
  noColor: 'Default color',
  openCard: 'Open board card',
  pauseProject: 'Pause project',
  resumeProject: 'Resume project',
  deleteProject: 'Delete project',
  deleteConfirm: 'Delete',
  deleteProjectTitle: name => `Delete project ${name}?`,
  deleteProjectBody: 'Its missions and conversation are not touched; the project disappears from all boards.',
  deleteProjectDone: name => `Removed “${name}” — its missions and conversation are kept.`,
  renameProject: 'Rename project',
  archiveProject: 'Archive project',
  renameConfirm: 'Rename',
  renameProjectTitle: name => `Rename ${name}`,
  renamePlaceholder: 'Project name',
  liveMissions: 'Missions',
  noMissions: 'No recent missions.',
  close: 'Close',
  needsYouTag: 'needs you',
  pausedByYou: 'Paused by you',
  controllerStopped: cause => (cause ? `Controller stopped itself: ${cause}` : 'Controller stopped itself'),
  controllerSilent: since => `Controller silent since ${since}`
}

/** Registered via `ctx.i18n.register` at plugin load (disposer tracked). */
export const BOARD_LOCALES: PluginLocaleBundles = { en }

type Bound<T> = {
  [K in keyof T]: T[K] extends (...args: infer A) => string
    ? (...args: A) => string
    : T[K] extends object
      ? Bound<T[K]>
      : string
}

function bind<T extends object>(t: PluginTranslate, template: T, prefix = ''): Bound<T> {
  const out = {} as Record<string, unknown>

  for (const [key, value] of Object.entries(template)) {
    const path = prefix ? `${prefix}.${key}` : key
    out[key] =
      typeof value === 'function'
        ? (...args: unknown[]) => t(path, ...args)
        : value && typeof value === 'object'
          ? bind(t, value as object, path)
          : t(path)
  }

  return out as Bound<T>
}

export type BoardText = Bound<BoardMessages>

/** The board strings for the active locale — one hook every component reads. */
export function useBoard(): BoardText {
  const t = usePluginI18n('projects-board')

  return useMemo(() => bind(t, en), [t])
}

export const bucketLabel = (b: BoardText, name: string) => b.col[name as keyof BoardText['col']]?.label ?? name
export const bucketHelp = (b: BoardText, name: string) => b.col[name as keyof BoardText['col']]?.help ?? ''
