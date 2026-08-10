/**
 * Delivery indicator on a user bubble — reads the send-receipts ledger by the
 * bubble's message id. Pending shows a quiet spinner, delivered a brief muted
 * check, failed a red retry affordance whose click re-submits the SAME text
 * through the composer's external-submit door and consumes the receipt.
 * Nothing rendered for messages that aren't being tracked (history, streams).
 */

import { useStore } from '@nanostores/react'

import { requestComposerSubmit } from '@/app/chat/composer/focus'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { $sendReceipts, takeFailedSend } from '@/store/send-receipts'

export function SendReceiptIndicator({ messageId }: { messageId: null | string | undefined }) {
  const { t } = useI18n()
  const receipts = useStore($sendReceipts)
  const receipt = messageId ? receipts[messageId] : undefined

  if (!receipt) {
    return null
  }

  if (receipt.state === 'failed') {
    return (
      <button
        aria-label={t.desktop.sendFailedRetry}
        className="inline-flex items-center gap-1 self-end text-[0.6875rem] font-medium text-destructive transition-opacity hover:opacity-80"
        onClick={() => {
          const text = takeFailedSend(receipt.id)

          if (text) {
            requestComposerSubmit(text)
          }
        }}
        title={t.desktop.sendFailedRetry}
        type="button"
      >
        <Codicon name="warning" size="0.75rem" />
        {t.desktop.sendFailedRetry}
      </button>
    )
  }

  return (
    <span
      aria-label={receipt.state === 'pending' ? t.desktop.sendPending : t.desktop.sendDelivered}
      className="inline-flex items-center self-end text-(--ui-text-quaternary)"
      title={receipt.state === 'pending' ? t.desktop.sendPending : t.desktop.sendDelivered}
    >
      <Codicon
        name={receipt.state === 'pending' ? 'loading' : 'check'}
        size="0.7rem"
        spinning={receipt.state === 'pending'}
      />
    </span>
  )
}
