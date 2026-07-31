export const REMOTE_LIVENESS_TIMEOUT_MS = 10_000
export const REMOTE_LIVENESS_FAILURE_LIMIT = 3
// [fork-delta] A resolver flap (Tailscale MagicDNS restarting, captive portal,
// VPN handover) fails getaddrinfo while the cached transport is often still
// healthy. Dropping the connection after 3 such probes strands every open
// session window (observed 2026-07-31: ENOTFOUND agent-backend.thomas.md for
// ~2 minutes killed a session mid-creation). Resolver-class failures therefore
// tolerate twice the streak before the connection is torn down.
export const REMOTE_LIVENESS_RESOLVER_FAILURE_LIMIT = REMOTE_LIVENESS_FAILURE_LIMIT * 2

const RESOLVER_ERROR_CODES = new Set(['ENOTFOUND', 'EAI_AGAIN', 'EAI_FAIL', 'EAI_NONAME'])

export function isResolverError(error: unknown): boolean {
  if (!error || typeof error !== 'object') {
    return false
  }
  const code = (error as { code?: unknown }).code
  if (typeof code === 'string' && RESOLVER_ERROR_CODES.has(code)) {
    return true
  }
  const cause = (error as { cause?: unknown }).cause
  if (cause && cause !== error && isResolverError(cause)) {
    return true
  }
  const message = (error as { message?: unknown }).message
  return typeof message === 'string' && /\b(ENOTFOUND|EAI_AGAIN|EAI_FAIL|EAI_NONAME)\b/.test(message)
}
// Even at the capped retry path, consecutive liveness observations are at most
// about 48s apart (ticket mint + socket open + backoff + the next status probe).
// One minute keeps a continuous outage together without carrying old failures.
export const REMOTE_LIVENESS_FAILURE_WINDOW_MS = 60_000

export interface RemoteLivenessFailure {
  failures: number
  shouldReset: boolean
}

interface RemoteConnectionDescriptor {
  baseUrl?: null | string
  mode?: null | string
}

export interface RevalidateRemoteConnectionOptions<TConnection extends RemoteConnectionDescriptor> {
  connectionPromise: Promise<TConnection>
  currentConnectionPromise: () => null | Promise<TConnection>
  log: (message: string) => void
  probe: (url: string, options: { timeoutMs: number }) => Promise<unknown>
  resetConnection: () => void
  tracker: RemoteLivenessTracker
}

export interface RemoteRevalidationResult {
  ok: true
  rebuilt: boolean
}

/**
 * Coalesces revalidation work for one cached connection promise.
 *
 * Every Desktop BrowserWindow owns a renderer gateway loop. When several
 * windows observe the same disconnect they can all ask the Electron main
 * process to revalidate the shared primary connection at once. Those calls
 * must count as one probe, not several consecutive failures.
 */
export class RemoteRevalidationCoordinator {
  readonly #inflightByConnection = new WeakMap<object, Promise<unknown>>()

  run<T>(connection: object, task: () => Promise<T>): Promise<T> {
    const existing = this.#inflightByConnection.get(connection) as Promise<T> | undefined

    if (existing) {
      return existing
    }

    const pending = Promise.resolve().then(task)

    const clear = () => {
      if (this.#inflightByConnection.get(connection) === pending) {
        this.#inflightByConnection.delete(connection)
      }
    }

    this.#inflightByConnection.set(connection, pending)
    // Clean up on both outcomes without creating an unhandled rejected branch.
    void pending.then(clear, clear)

    return pending
  }
}

/**
 * Tracks consecutive remote liveness failures independently per gateway.
 * A successful probe clears the streak, and reaching the limit consumes it so
 * a rebuilt connection starts from a clean state.
 */
export class RemoteLivenessTracker {
  readonly #failureLimit: number
  readonly #failureWindowMs: number
  readonly #failuresByBaseUrl = new Map<string, { failures: number; lastFailureAt: number }>()
  readonly #now: () => number

  constructor(
    failureLimit = REMOTE_LIVENESS_FAILURE_LIMIT,
    failureWindowMs = REMOTE_LIVENESS_FAILURE_WINDOW_MS,
    now: () => number = Date.now
  ) {
    if (!Number.isInteger(failureLimit) || failureLimit < 1) {
      throw new Error('Remote liveness failure limit must be a positive integer.')
    }

    if (!Number.isFinite(failureWindowMs) || failureWindowMs < 1) {
      throw new Error('Remote liveness failure window must be positive.')
    }

    this.#failureLimit = failureLimit
    this.#failureWindowMs = failureWindowMs
    this.#now = now
  }

  recordSuccess(baseUrl: string): void {
    this.#failuresByBaseUrl.delete(baseUrl)
  }

  recordFailure(baseUrl: string, options?: { failureLimit?: number }): RemoteLivenessFailure {
    const now = this.#now()
    const previous = this.#failuresByBaseUrl.get(baseUrl)
    const withinFailureWindow = previous && now - previous.lastFailureAt <= this.#failureWindowMs
    const failures = (withinFailureWindow ? previous.failures : 0) + 1
    // The CURRENT probe's error class picks the threshold. A mixed streak
    // (transport error after resolver errors) intentionally applies the
    // stricter transport limit immediately.
    const effectiveLimit = options?.failureLimit ?? this.#failureLimit
    const shouldReset = failures >= effectiveLimit

    if (shouldReset) {
      this.#failuresByBaseUrl.delete(baseUrl)
    } else {
      this.#failuresByBaseUrl.set(baseUrl, { failures, lastFailureAt: now })
    }

    return { failures, shouldReset }
  }

  clear(): void {
    this.#failuresByBaseUrl.clear()
  }
}

export interface PooledRemoteEntry {
  process?: unknown
  remoteBaseUrl?: null | string
}

export interface RevalidatePooledRemoteBackendsOptions {
  entries: Iterable<[string, PooledRemoteEntry]>
  log: (message: string) => void
  probe: (url: string, options: { timeoutMs: number }) => Promise<unknown>
  stopBackend: (profile: string) => void
  tracker: RemoteLivenessTracker
}

/**
 * Probe pooled REMOTE descriptors and drop the dead ones.
 *
 * A pooled entry backed by a remote host has no child process, so the 'exit'
 * handler that clears a dead local backend never fires, and the renderer's
 * keepalive touch keeps the idle reaper off it. Without this the pool serves a
 * descriptor for an unreachable host indefinitely.
 *
 * Entries share the primary's failure policy, keyed per base URL, so a profile
 * pointing at the same host as another does not burn the streak twice as fast.
 */
export async function revalidatePooledRemoteBackends({
  entries,
  log,
  probe,
  stopBackend,
  tracker
}: RevalidatePooledRemoteBackendsOptions): Promise<{ dropped: string[] }> {
  const remotes = [...entries].filter(([, entry]) => !entry.process && entry.remoteBaseUrl)
  const dropped: string[] = []

  await Promise.all(
    remotes.map(async ([profile, entry]) => {
      const baseUrl = String(entry.remoteBaseUrl).replace(/\/+$/, '')

      try {
        await probe(`${baseUrl}/api/status`, { timeoutMs: REMOTE_LIVENESS_TIMEOUT_MS })
        tracker.recordSuccess(baseUrl)
      } catch {
        const failure = tracker.recordFailure(baseUrl)

        if (!failure.shouldReset) {
          log(
            `Pooled remote backend for profile "${profile}" failed liveness probe (${failure.failures}/${REMOTE_LIVENESS_FAILURE_LIMIT}); keeping descriptor for retry.`
          )

          return
        }

        log(`Pooled remote backend for profile "${profile}" failed liveness probe; dropping stale descriptor.`)
        stopBackend(profile)
        dropped.push(profile)
      }
    })
  )

  return { dropped }
}

/**
 * Probe the cached primary remote connection and apply the failure policy.
 * The caller owns single-flight coordination; identity checks here ensure an
 * old async result cannot mutate or reset a replacement connection.
 */
export async function revalidateRemoteConnection<TConnection extends RemoteConnectionDescriptor>({
  connectionPromise,
  currentConnectionPromise,
  log,
  probe,
  resetConnection,
  tracker
}: RevalidateRemoteConnectionOptions<TConnection>): Promise<RemoteRevalidationResult> {
  let connection: TConnection

  try {
    connection = await connectionPromise
  } catch {
    // The cached boot already rejected; its own recovery path will clear it.
    return { ok: true, rebuilt: false }
  }

  if (currentConnectionPromise() !== connectionPromise) {
    return { ok: true, rebuilt: false }
  }

  if (connection.mode !== 'remote' || !connection.baseUrl) {
    return { ok: true, rebuilt: false }
  }

  const baseUrl = connection.baseUrl.replace(/\/+$/, '')

  try {
    await probe(`${baseUrl}/api/status`, { timeoutMs: REMOTE_LIVENESS_TIMEOUT_MS })

    if (currentConnectionPromise() !== connectionPromise) {
      return { ok: true, rebuilt: false }
    }

    tracker.recordSuccess(baseUrl)

    return { ok: true, rebuilt: false }
  } catch (error) {
    if (currentConnectionPromise() !== connectionPromise) {
      return { ok: true, rebuilt: false }
    }

    const resolverError = isResolverError(error)
    const failureLimit = resolverError ? REMOTE_LIVENESS_RESOLVER_FAILURE_LIMIT : REMOTE_LIVENESS_FAILURE_LIMIT
    const failure = tracker.recordFailure(baseUrl, { failureLimit })

    if (!failure.shouldReset) {
      log(
        `Cached remote Hermes backend failed liveness probe (${failure.failures}/${failureLimit}${resolverError ? '; resolver error — DNS flap tolerated' : ''}); keeping connection for retry.`
      )

      return { ok: true, rebuilt: false }
    }

    log('Cached remote Hermes backend failed liveness probe; dropping stale connection.')
    resetConnection()

    return { ok: true, rebuilt: true }
  }
}
