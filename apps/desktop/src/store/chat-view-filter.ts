import { type Codec, persistentAtom } from '@/lib/persisted'

/**
 * Transcript view filter for sessions that receive scheduled deliveries:
 *  - `all`     — every row, deliveries collapsed into digest runs
 *  - `mine`    — hides cron/callback reports unless they need the owner or
 *                answer a user message directly
 *  - `reports` — only deliveries and the user messages around them
 */
export type ChatViewFilter = 'all' | 'mine' | 'reports'

export const CHAT_VIEW_FILTERS: readonly ChatViewFilter[] = ['all', 'mine', 'reports']

const STORAGE_KEY = 'hermes.desktop.chatViewFilter'

const filterCodec: Codec<ChatViewFilter> = {
  decode: raw => (raw === 'mine' || raw === 'reports' ? raw : 'all'),
  encode: value => value
}

export const $chatViewFilter = persistentAtom<ChatViewFilter>(STORAGE_KEY, 'all', filterCodec)

export function setChatViewFilter(filter: ChatViewFilter) {
  $chatViewFilter.set(filter)
}
