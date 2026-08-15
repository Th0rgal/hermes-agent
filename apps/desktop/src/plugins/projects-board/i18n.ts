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
  renameProject: string
  renameConfirm: string
  renamePlaceholder: string
  renameProjectTitle: (current: string) => string
  deleteProject: string
  deleteConfirm: string
  deleteConfirmBody: string
  deleteConfirmTitle: (name: string) => string
  pausedByYou: string
  controllerStopped: (cause: string) => string
  controllerWaiting: (cause: string) => string
  controllerSilent: (since: string) => string
  controllerCheckedTip: (ago: string) => string
  controllerNeverEngaged: string
  controllerDegraded: (reason: 'dropped' | 'misrouted' | 'missing') => string
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
  grantEdit: string
  grantUnset: string
  mergeAuthority: string
  mergeAuthorityTip: string
  mergeSummary: (value: string) => string
  budgetSummary: (value: string) => string
  parallelSummary: (n: number) => string
  merge: Record<'custom' | 'full' | 'repos' | 'review-first', string>
  mergeReposPlaceholder: string
  mergeCustomPlaceholder: string
  budgetPerTick: string
  budgetPerTickTip: string
  budget: Record<'1 mission' | '2 missions' | 'custom' | 'unbounded', string>
  budgetCustomPlaceholder: string
  parallelMissions: string
  parallelMissionsTip: string
  parallelUnlimited: string
  pauseReason: string
  resumeCondition: string
  materialBar: string
  saveGrant: string
  grantSaved: string
  openDecisions: string
  autonomyLevel: string
  autonomyLevelUnset: string
  autonomy: Record<'act_full' | 'act_reversible' | 'observe' | 'propose', string>
  autonomyTip: Record<'act_full' | 'act_reversible' | 'observe' | 'propose', string>
  needsYouSection: string
  needsYouEmpty: string
  answerPlaceholder: string
  answerSend: string
  answerDelivered: string
  answerRecorded: string
  decisionsBadge: (n: number) => string
  railHide: string
  railShow: string
  roadmap: string
  roadmapEmpty: string
  roadmapUnavailable: string
  roadmapProgress: (done: number, total: number) => string
  taskAttempts: (n: number) => string
  openPr: string
  workerMission: string
  acceptanceCriteria: string
  recentActivity: string
  recentActivityEmpty: string
  answeredLabel: string
  decidedLabel: string
  debugSection: string
  stateTimeline: string
  observations: (n: number) => string
  conversation: string
  openConversation: string
  unread: (n: number) => string
  menuOptions: string
  nextActionArrow: (action: string) => string
  focusAttentionTip: string
  paletteOpenConversation: (label: string) => string
  paletteOpenCard: (label: string) => string
  steerMissionTip: string
  attentionNotifTitle: string
  attentionNotifBody: (label: string) => string
  openBoardAction: string
  notifyToggleOn: string
  notifyToggleOff: string
  setColor: string
  noColor: string
  openCard: string
  openItems: string
  controllerBehind: string
  lastSignal: string
  pauseProject: string
  resumeProject: string
  liveMissions: string
  noMissions: string
  close: string
  needsYouTag: string
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
  renameProject: 'Rename…',
  renameConfirm: 'Rename',
  renamePlaceholder: 'Project name',
  renameProjectTitle: current => `Rename “${current}”`,
  deleteProject: 'Delete…',
  deleteConfirm: 'Delete',
  deleteConfirmBody: 'Removes the project from the board. Its missions and history are kept.',
  deleteConfirmTitle: name => `Delete “${name}”?`,
  pausedByYou: 'Paused by you',
  controllerStopped: cause => (cause ? `Controller stopped itself: ${cause}` : 'Controller stopped itself'),
  controllerWaiting: cause => (cause ? `Controller is waiting: ${cause}` : 'Controller is waiting'),
  controllerSilent: since => `Controller silent since ${since}`,
  controllerCheckedTip: ago => `Controller checked ${ago} — nothing new to report`,
  controllerNeverEngaged: 'Controller attached but never engaged — nothing has run yet',
  controllerDegraded: reason =>
    reason === 'missing'
      ? 'Active, but no controller is running — nothing drives this project'
      : reason === 'dropped'
        ? 'Controller output is reaching no conversation'
        : 'Controller output lands in a throwaway session, not the project conversation',
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
  grantHint: 'Guidance the controller follows, not a hard limit — the scheduler does not enforce it.',
  grantEdit: 'Edit grant',
  grantUnset: "controller's judgment",
  mergeAuthority: 'Merge authority',
  mergeAuthorityTip: 'Whether the controller may merge PRs itself, or must wait for your review.',
  mergeSummary: value => `merge: ${value}`,
  budgetSummary: value => `budget: ${value}`,
  parallelSummary: n => `parallel: ${n}`,
  merge: {
    custom: 'Custom…',
    full: 'Merges freely',
    repos: 'Only these repos…',
    'review-first': 'Review first'
  },
  mergeReposPlaceholder: 'owner/repo, owner/other',
  mergeCustomPlaceholder: 'custom merge rule',
  budgetPerTick: 'Budget per tick',
  budgetPerTickTip: 'How much new work the controller may start each time it wakes.',
  budget: {
    '1 mission': '1 mission',
    '2 missions': '2 missions',
    custom: 'Custom…',
    unbounded: 'Unbounded'
  },
  budgetCustomPlaceholder: 'e.g. 2 missions',
  parallelMissions: 'Parallel missions',
  parallelMissionsTip: 'Maximum missions the controller may keep running at once.',
  parallelUnlimited: 'unlimited',
  pauseReason: 'Pause reason',
  resumeCondition: 'Resume condition',
  materialBar: 'Material bar',
  saveGrant: 'Save grant',
  grantSaved: 'Grant updated.',
  openDecisions: 'Open decisions',
  autonomyLevel: 'Autonomy',
  autonomyLevelUnset: 'Not set',
  autonomy: {
    act_full: 'acts freely',
    act_reversible: 'acts (reversible)',
    observe: 'observes',
    propose: 'proposes'
  },
  autonomyTip: {
    act_full: 'The controller acts without asking, including irreversible steps.',
    act_reversible: 'The controller acts without asking, except irreversible steps.',
    observe: 'The controller only reports — every action needs you.',
    propose: 'The controller escalates every consequential action to you.'
  },
  needsYouSection: 'Needs you',
  needsYouEmpty: 'Nothing waiting on you.',
  answerPlaceholder: 'Your decision…',
  answerSend: 'Answer',
  answerDelivered: 'Answer recorded and delivered to the controller.',
  answerRecorded: 'Answer recorded — the controller reads it next tick.',
  decisionsBadge: n => `${n} decision${n === 1 ? '' : 's'}`,
  railHide: 'Hide project panel',
  railShow: 'Show project panel',
  roadmap: 'Roadmap',
  roadmapEmpty: 'No planned tasks yet.',
  roadmapUnavailable: 'Roadmap temporarily unavailable — retrying.',
  roadmapProgress: (done, total) => `${done}/${total} done`,
  taskAttempts: n => `${n} attempt${n === 1 ? '' : 's'}`,
  openPr: 'Open PR',
  workerMission: 'Worker mission',
  acceptanceCriteria: 'Acceptance criteria',
  recentActivity: 'Recent activity',
  recentActivityEmpty: 'No recorded decisions yet.',
  answeredLabel: 'answered',
  decidedLabel: 'autonomous',
  debugSection: 'Debug',
  stateTimeline: 'State timeline',
  observations: n => `${n} observation${n === 1 ? '' : 's'}`,
  conversation: 'Conversation',
  openConversation: 'Open conversation',
  unread: n => `${n} unread message${n === 1 ? '' : 's'}`,
  menuOptions: 'Project options',
  nextActionArrow: action => `→ ${action}`,
  focusAttentionTip: 'Show projects needing attention',
  paletteOpenConversation: label => `Project: ${label} — open conversation`,
  paletteOpenCard: label => `Project: ${label} — open board card`,
  steerMissionTip: 'Steer this mission',
  attentionNotifTitle: 'Project needs attention',
  attentionNotifBody: label => `${label} needs your attention`,
  openBoardAction: 'Open board',
  notifyToggleOn: 'Attention notifications on — click to mute',
  notifyToggleOff: 'Attention notifications off — click to enable',
  setColor: 'Set color',
  noColor: 'Default color',
  openCard: 'Open board card',
  openItems: 'Open items',
  controllerBehind: 'controller behind',
  lastSignal: 'last signal',
  pauseProject: 'Pause project',
  resumeProject: 'Resume project',
  liveMissions: 'Missions',
  noMissions: 'No recent missions.',
  close: 'Close',
  needsYouTag: 'needs you'
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
