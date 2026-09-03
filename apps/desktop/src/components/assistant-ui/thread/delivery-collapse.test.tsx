// Controller deliveries (cron drops, mission callbacks) collapse to a one-line
// notice once they settle, so a control conversation reads as a log. Three
// things keep the body visible: the delivery is still streaming, the body is
// waiting on the owner (delivery.needsOwner), or the user prefers expanded
// deliveries ($deliveryCollapse). The divider pill stays above in every state.
import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $deliveryCollapse } from '@/store/delivery-collapse'

import { createdAt, stubThreadEnvironment } from '../test-utils'

import { Thread } from '.'

stubThreadEnvironment()

const arrival = createdAt.getTime() / 1000

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  $deliveryCollapse.set('collapsed')
})

function user(id: string, text: string): ThreadMessage {
  return {
    id,
    role: 'user',
    content: [{ type: 'text', text }],
    attachments: [],
    createdAt,
    metadata: { custom: {} }
  } as ThreadMessage
}

function delivery(text: string, { needsOwner = false, running = false } = {}): ThreadMessage {
  return {
    id: 'd1',
    role: 'assistant',
    content: [{ type: 'text', text }],
    status: running ? { type: 'running' } : { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: { delivery: { kind: 'cron', label: 'verity controller', needsOwner }, timelineTimestamp: arrival }
    }
  } as ThreadMessage
}

function Harness({ messages }: { messages: ThreadMessage[] }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages,
    isRunning: messages.at(-1)?.status?.type === 'running',
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

const BODY = 'PR #2332 still repairing.\n\nAction Thomas : aucune.'

describe('delivery collapse gate', () => {
  it('collapses a settled delivery by default, keeping the divider above it', async () => {
    const { container } = render(<Harness messages={[user('u1', 'tick'), delivery(BODY)]} />)

    await screen.findAllByText('PR #2332 still repairing.')
    const summary = container.querySelector('[data-slot="aui_assistant-delivery-collapsed"] summary')

    expect(summary?.textContent).toContain('PR #2332 still repairing.')
    expect(container.querySelector('[data-slot="aui_assistant-delivery-divider"]')).toBeTruthy()
  })

  it('stays expanded when the delivery needs the owner', async () => {
    const { container } = render(<Harness messages={[user('u1', 'tick'), delivery(BODY, { needsOwner: true })]} />)

    await screen.findByText(/still repairing/)
    expect(container.querySelector('[data-slot="aui_assistant-delivery-collapsed"]')).toBeNull()
    expect(container.querySelector('[data-slot="aui_assistant-delivery-divider"]')).toBeTruthy()
  })

  it('stays expanded when the preference is expanded', async () => {
    $deliveryCollapse.set('expanded')
    const { container } = render(<Harness messages={[user('u1', 'tick'), delivery(BODY)]} />)

    await screen.findByText(/still repairing/)
    expect(container.querySelector('[data-slot="aui_assistant-delivery-collapsed"]')).toBeNull()
  })

  it('never collapses while the delivery is still streaming', async () => {
    const { container } = render(<Harness messages={[user('u1', 'tick'), delivery('working', { running: true })]} />)

    await screen.findByText('working')
    expect(container.querySelector('[data-slot="aui_assistant-delivery-collapsed"]')).toBeNull()
    expect(container.querySelector('[data-message-streaming="true"]')).toBeTruthy()
  })
})
