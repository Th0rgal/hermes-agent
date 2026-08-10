import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { onComposerSubmitRequest } from '@/app/chat/composer/focus'
import { $sendReceipts, markSendFailed, trackSend } from '@/store/send-receipts'

import { SendReceiptIndicator } from './send-receipt'

afterEach(() => {
  cleanup()
  $sendReceipts.set({})
})

describe('SendReceiptIndicator', () => {
  it('renders nothing for untracked messages', () => {
    const { container } = render(<SendReceiptIndicator messageId="unknown" />)

    expect(container.textContent).toBe('')
  })

  it('shows the pending spinner while the ack is outstanding', () => {
    trackSend('m1', 'hello', 'sess')

    render(<SendReceiptIndicator messageId="m1" />)

    expect(screen.getByLabelText('Sending…')).toBeTruthy()
  })

  it('failed → retry resubmits the SAME text through the composer and consumes the receipt', async () => {
    trackSend('m2', 'exact words', 'sess')
    markSendFailed('m2')

    const seen: string[] = []

    const unsubscribe = onComposerSubmitRequest(({ text }) => {
      seen.push(text)
    })

    render(<SendReceiptIndicator messageId="m2" />)

    fireEvent.click(screen.getByLabelText('Not delivered — retry'))

    // The composer dispatch is deferred a tick (setTimeout 0).
    await waitFor(() => expect(seen).toEqual(['exact words']))
    expect($sendReceipts.get().m2).toBeUndefined()

    unsubscribe()
  })
})
