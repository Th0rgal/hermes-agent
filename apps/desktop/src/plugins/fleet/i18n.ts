/** Fleet plugin locale bundle (English default). */
export const FLEET_LOCALES = {
  en: {
    title: 'Fleet',
    open: 'Fleet: Open',
    empty: 'No sandboxed.sh projects on this host.',
    unreachable: 'sandboxed.sh is unreachable.',
    agents: (n: number) => `${n} agent${n === 1 ? '' : 's'}`,
    needsYou: (n: number) => `${n} need${n === 1 ? 's' : ''} you`,
    active: 'active',
    blocked: 'blocked',
    paused: 'paused',
    steerPlaceholder: 'Nudge this agent…',
    steer: 'Send',
    countTip: (active: number, attention: number) =>
      attention > 0
        ? `${active} agents running · ${attention} need you`
        : `${active} agents running`
  }
}
