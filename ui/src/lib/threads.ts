import type { ID, ThreadId } from '../contract/types'

export const EXEC_ID = 'exec'
export const EXEC_THREAD: ThreadId = 'executive'
const BRANCH_MARKER = ':branch:'
const WAR_ROOM_THREAD_PREFIX = 'war:'
const MEETING_THREAD_PREFIX = 'meet:'

export function threadIdFor(deptId: ID): ThreadId {
  return deptId === EXEC_ID ? EXEC_THREAD : `dept:${deptId}`
}

export function warRoomThreadId(warRoomId: ID): ThreadId {
  return `${WAR_ROOM_THREAD_PREFIX}${warRoomId}`
}

export function meetingThreadId(meetingId: ID): ThreadId {
  return `${MEETING_THREAD_PREFIX}${meetingId}`
}

export function deptIdFromThread(threadId: ThreadId): ID {
  if (threadId.includes(BRANCH_MARKER)) {
    threadId = threadId.split(BRANCH_MARKER)[0] as ThreadId
  }
  if (threadId === EXEC_THREAD) return EXEC_ID
  return threadId.startsWith('dept:') ? threadId.slice(5) : threadId
}

export function isExec(deptId: ID): boolean {
  return deptId === EXEC_ID
}
