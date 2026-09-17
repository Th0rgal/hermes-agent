import type { DesktopRegistryConnection, HermesConnection } from '@/global'

/** Where the assistant's files actually live. */
export type WorkspaceKind = 'container' | 'host' | 'unknown'

const CONTAINER_PATH_MARKERS = [
  /(^|\/)workspaces\//i,
  /(^|\/)workspace\//i,
  /(^|\/)mission-[0-9a-f-]{8,}\b/i,
  /(^|\/)\.sandboxed(-sh)?\//i,
  /\/nspawn\//i,
  /\/var\/lib\/machines\//i
]

/** True when `cwd` looks like an nspawn/Docker/sandboxed mission tree, not a host checkout. */
export function classifyWorkspacePath(cwd: string | null | undefined): WorkspaceKind {
  const path = cwd?.trim()

  if (!path) {
    return 'unknown'
  }

  if (CONTAINER_PATH_MARKERS.some(marker => marker.test(path))) {
    return 'container'
  }

  return 'host'
}

function hostnameFromUrl(url: string | null | undefined): string | null {
  if (!url) {
    return null
  }

  try {
    return new URL(url).hostname || null
  } catch {
    return null
  }
}

/** Human machine name for the live gateway: this Mac, an SSH host, or a remote URL. */
export function machineLabel(
  connection: Pick<HermesConnection, 'baseUrl' | 'mode' | 'remoteHost' | 'remoteKind'> | null | undefined,
  thisMac = 'This Mac'
): string {
  if (!connection || connection.mode !== 'remote') {
    return thisMac
  }

  const host = connection.remoteHost?.trim() || hostnameFromUrl(connection.baseUrl)

  if (!host) {
    return connection.remoteKind === 'cloud' ? 'Cloud' : 'Remote'
  }

  return host
}

export interface SessionLocation {
  kind: WorkspaceKind
  label: string
  machine: string
  native: boolean
  virtualized: boolean
  workspace: string
}

function pathLeaf(path: string): string {
  const trimmed = path.replace(/\/+$/, '')
  const parts = trimmed.split(/[\\/]/).filter(Boolean)

  return parts[parts.length - 1] || trimmed
}

/** Compact "machine · workspace" line for the session header. */
export function describeSessionLocation(options: {
  connection?: Pick<HermesConnection, 'baseUrl' | 'mode' | 'remoteHost' | 'remoteKind'> | null
  cwd?: null | string
  thisMac?: string
}): SessionLocation {
  const cwd = options.cwd?.trim() || ''
  const kind = classifyWorkspacePath(cwd)
  const native = options.connection?.mode !== 'remote'
  const machine = machineLabel(options.connection, options.thisMac ?? 'This Mac')
  const workspace = cwd ? pathLeaf(cwd) : ''
  const label = workspace ? `${machine} · ${workspace}` : machine

  return {
    kind,
    label,
    machine,
    native,
    virtualized: !native || kind === 'container',
    workspace
  }
}

export function localRegistryConnection(
  connections: readonly DesktopRegistryConnection[] | null | undefined
): DesktopRegistryConnection | null {
  return connections?.find(connection => connection.kind === 'local') ?? null
}
