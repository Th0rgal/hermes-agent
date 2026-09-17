import { useStore } from '@nanostores/react'
import { useCallback } from 'react'

import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { FolderOpen, Monitor, Terminal } from '@/lib/icons'
import { describeSessionLocation, localRegistryConnection } from '@/lib/session-location'
import { cn } from '@/lib/utils'
import { $connectionsRegistry, selectConnection } from '@/store/connections'
import { $connection, $currentCwd } from '@/store/session'

/**
 * Always-visible "which machine / which folder" chip for the open chat.
 *
 * The statusbar already has connection + cwd items, but it is opt-in. This chip
 * lives in the session header so a remote container vs this Mac is never a
 * mystery. When the live cwd looks virtualized and a native local connection
 * exists, offer "Run on this Mac".
 */
export function SessionLocationChip({ className, cwd }: { className?: string; cwd?: null | string }) {
  const { t } = useI18n()
  const connection = useStore($connection)
  const liveCwd = useStore($currentCwd)
  const registry = useStore($connectionsRegistry)
  const path = (cwd ?? liveCwd)?.trim() || ''
  const location = describeSessionLocation({
    connection,
    cwd: path,
    thisMac: t.statusbar.thisMac
  })
  const nativeTarget = localRegistryConnection(registry?.connections)
  const canRunNatively = Boolean(nativeTarget && location.virtualized && nativeTarget.id !== connection?.connectionId)

  const runNatively = useCallback(() => {
    if (!nativeTarget) {
      return
    }

    void selectConnection(nativeTarget.id)
  }, [nativeTarget])

  const Icon = location.native ? Monitor : location.kind === 'container' ? Terminal : FolderOpen
  const tip = canRunNatively
    ? t.statusbar.runNativelyHint
    : path
      ? `${location.machine}\n${path}`
      : location.machine

  return (
    <Tip content={tip}>
      <button
        className={cn(
          'pointer-events-auto mr-1.5 inline-flex max-w-56 shrink items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground hover:bg-accent/60 hover:text-foreground',
          location.virtualized && 'text-amber-700 hover:text-amber-700 dark:text-amber-400',
          className
        )}
        data-location-kind={location.kind}
        data-location-native={location.native ? 'true' : 'false'}
        data-slot="session-location"
        onClick={canRunNatively ? runNatively : undefined}
        type="button"
      >
        <Icon className="size-3 shrink-0" />
        <span className="truncate">{location.label}</span>
        {canRunNatively ? <span className="truncate font-medium">{t.statusbar.runNatively}</span> : null}
      </button>
    </Tip>
  )
}
