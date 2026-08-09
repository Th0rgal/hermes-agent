import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { QueueBanner } from './queue-banner'

afterEach(cleanup)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      composer: {
        queueWaiting: (count: number) => (count === 1 ? '1 message waiting' : `${count} messages waiting`)
      }
    }
  })
}))

describe('QueueBanner', () => {
  it('shows the waiting count while prompts are queued', () => {
    const { getByText } = render(<QueueBanner count={3} onFocusQueue={() => undefined} />)

    expect(getByText('3 messages waiting')).toBeTruthy()
  })

  it('renders nothing once the queue drains', () => {
    const { container, rerender } = render(<QueueBanner count={1} onFocusQueue={() => undefined} />)

    expect(container.querySelector('[data-slot="queue-banner"]')).toBeTruthy()

    rerender(<QueueBanner count={0} onFocusQueue={() => undefined} />)

    expect(container.querySelector('[data-slot="queue-banner"]')).toBeNull()
  })

  it('clicking focuses the queue/composer', () => {
    const onFocusQueue = vi.fn()
    const { getByText } = render(<QueueBanner count={2} onFocusQueue={onFocusQueue} />)

    fireEvent.click(getByText('2 messages waiting'))

    expect(onFocusQueue).toHaveBeenCalledTimes(1)
  })
})
