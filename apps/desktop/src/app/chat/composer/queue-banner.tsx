import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'

interface QueueBannerProps {
  count: number
  /** Bring the user to the queue: focus the composer (the queue panel sits
   *  directly above it in the status stack). */
  onFocusQueue: () => void
}

/**
 * Persistent, non-blocking banner shown inside the conversation view while the
 * session has queued prompts — the toast PR #74 added is transient, and once it
 * fades nothing in the viewport says turns are still waiting. Renders nothing
 * when the queue is empty, so draining the queue clears it on its own.
 */
export function QueueBanner({ count, onFocusQueue }: QueueBannerProps) {
  const { t } = useI18n()

  if (count <= 0) {
    return null
  }

  return (
    <button
      className="flex w-full items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--dt-composer-ring)_32%,transparent)] bg-accent/18 px-2 py-1 text-left text-[0.7rem] text-muted-foreground/88 transition-colors hover:bg-accent/30 hover:text-foreground/90"
      data-slot="queue-banner"
      onClick={onFocusQueue}
      type="button"
    >
      <Codicon className="shrink-0 text-muted-foreground/70" name="layers" size="0.8rem" />
      <span className="min-w-0 flex-1 truncate">{t.composer.queueWaiting(count)}</span>
    </button>
  )
}
