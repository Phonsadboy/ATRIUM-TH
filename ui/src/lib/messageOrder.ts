import type { ChatMessage } from '../contract/types'

/** A reply the agent is still working on (queued, sending, or token-streaming). */
export function isLiveReply(m: ChatMessage): boolean {
  return Boolean(m.pending || m.streaming)
}

/**
 * Chat display order: settled messages chronologically, then live replies
 * pinned at the bottom (like a typing indicator) so the working bubble is
 * always the latest thing on screen — even when its stored ts predates
 * messages that arrived while it was queued or streaming.
 */
export function compareChatOrder(a: ChatMessage, b: ChatMessage): number {
  const liveA = isLiveReply(a) ? 1 : 0
  const liveB = isLiveReply(b) ? 1 : 0
  if (liveA !== liveB) return liveA - liveB
  return a.ts - b.ts || a.id.localeCompare(b.id)
}
