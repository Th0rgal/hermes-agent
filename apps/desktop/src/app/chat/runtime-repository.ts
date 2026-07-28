import { ExportedMessageRepository, type ThreadMessage } from '@assistant-ui/react'
import { useMemo, useRef } from 'react'

import type { ChatMessage } from '@/lib/chat-messages'
import { coalesceToolOnlyAssistants, createToolMergeCache, toRuntimeMessage } from '@/lib/chat-runtime'

/**
 * ChatMessage[] -> assistant-ui message repository, with a WeakMap identity
 * cache so unchanged messages convert once (and a tool-merge cache that folds
 * tool-only assistant turns into their neighbour). Shared by the main chat's
 * runtime boundary and session tiles — one transcript pipeline, N surfaces.
 */
export function useRuntimeMessageRepository(messages: ChatMessage[]): ExportedMessageRepository {
  const cacheRef = useRef(new WeakMap<ChatMessage, ThreadMessage>())
  const toolMergeCacheRef = useRef(createToolMergeCache())

  return useMemo(() => {
    const items: { message: ThreadMessage; parentId: string | null }[] = []
    const branchParentByGroup = new Map<string, string | null>()
    const seenIds = new Set<string>()
    let visibleParentId: string | null = null
    let headId: string | null = null

    for (const message of coalesceToolOnlyAssistants(messages, toolMergeCacheRef.current)) {
      let parentId = visibleParentId

      if (message.role === 'assistant' && message.branchGroupId) {
        if (!branchParentByGroup.has(message.branchGroupId)) {
          branchParentByGroup.set(message.branchGroupId, visibleParentId)
        }

        parentId = branchParentByGroup.get(message.branchGroupId) ?? null
      }

      const cachedMessage = cacheRef.current.get(message)
      let runtimeMessage = cachedMessage ?? toRuntimeMessage(message)

      if (!cachedMessage) {
        cacheRef.current.set(message, runtimeMessage)
      }

      // A duplicate id makes MessageRepository.link throw and takes the whole
      // chat surface down in its error boundary — unrecoverably, since Retry
      // rebuilds the same repository. Ids are supposed to be unique upstream;
      // if one slips through anyway, render the message under a suffixed id
      // instead of crashing. Not cached: the remap depends on list position.
      if (seenIds.has(runtimeMessage.id)) {
        runtimeMessage = { ...runtimeMessage, id: `${runtimeMessage.id}~dup${items.length}` }
      }

      seenIds.add(runtimeMessage.id)
      items.push({ message: runtimeMessage, parentId })

      if (!message.hidden) {
        visibleParentId = runtimeMessage.id
        headId = runtimeMessage.id
      }
    }

    return ExportedMessageRepository.fromBranchableArray(items, { headId })
  }, [messages])
}
