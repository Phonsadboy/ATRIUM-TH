import type { ChatMessage } from '../contract/types'

/**
 * Only pin bubbles that are plausibly still working. Orphaned pending messages
 * (engine restarted mid-job, crashed stream) keep pending=true in storage until
 * the backend reaper closes them — without this window they would all pile up
 * at the bottom of the thread.
 */
export const LIVE_PIN_WINDOW_MS = 15 * 60_000

/** A reply the agent is still working on (queued, sending, or token-streaming). */
export function isLiveReply(m: ChatMessage, now: number = Date.now()): boolean {
  if (!m.pending && !m.streaming) return false
  return now - m.ts <= LIVE_PIN_WINDOW_MS
}

/**
 * Chat display order: settled messages chronologically, then live replies
 * pinned at the bottom (like a typing indicator) so the working bubble is
 * always the latest thing on screen — even when its stored ts predates
 * messages that arrived while it was queued or streaming.
 */
export function compareChatOrder(a: ChatMessage, b: ChatMessage, now: number = Date.now()): number {
  const liveA = isLiveReply(a, now) ? 1 : 0
  const liveB = isLiveReply(b, now) ? 1 : 0
  if (liveA !== liveB) return liveA - liveB
  return a.ts - b.ts || a.id.localeCompare(b.id)
}
