// Collision-proof ids for messages minted on the live event path (streamed
// bubbles, completions, inline errors, system lines). `Date.now()` alone is
// NOT unique: two events processed in the same millisecond (e.g. a burst of
// queued deliveries flushed on reconnect, or back-to-back completions) mint
// identical ids, and a duplicate id makes assistant-ui's MessageRepository
// throw while linking the thread — the whole chat surface dies in its error
// boundary ("… failed to render") and Retry rebuilds the same state forever.
let liveMessageIdSeq = 0

export const nextLiveMessageId = (prefix: string) => `${prefix}-${Date.now()}-${++liveMessageIdSeq}`
