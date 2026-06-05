import type { Originator, Task, TaskStatus } from '../contract/types'
import { ACCENT_HEX } from './visuals'

export const STATUS_LABEL: Record<TaskStatus, string> = {
  backlog: 'รอคิว',
  assigned: 'มอบหมายแล้ว',
  in_progress: 'กำลังทำ',
  review: 'ตรวจงาน',
  revising: 'แก้ตามรีวิว',
  waiting: 'รอการตอบกลับ',
  done: 'เสร็จ',
  blocked: 'ติดปัญหา',
  cancelled: 'ยกเลิก',
}

export const STATUS_HEX: Record<TaskStatus, string> = {
  backlog: '#7eb0dc',
  assigned: '#aba090',
  in_progress: '#57d6bf',
  review: '#f4a945',
  revising: '#e89a3c',
  waiting: '#b3a4ee',
  done: '#7bbf8f',
  blocked: '#f0735f',
  cancelled: '#9a958c',
}

/**
 * A task the user pauses from the control modal is carried as `status: 'blocked'` plus this
 * marker in `statusReason` (no dedicated enum). These helpers let the UI present it as a
 * deliberate pause ("หยุดชั่วคราว") instead of a real problem ("ติดปัญหา").
 */
export const PAUSED_BY_USER_REASON = 'paused_by_user'
export const PAUSED_LABEL = 'หยุดชั่วคราว'
export const PAUSED_HEX = ACCENT_HEX.honey

export function isUserPaused(task: Task): boolean {
  return task.status === 'blocked' && task.statusReason === PAUSED_BY_USER_REASON
}

/** Columns for the kanban board, in flow order. */
export const BOARD_COLUMNS: TaskStatus[] = [
  'assigned',
  'in_progress',
  'waiting',
  'review',
  'revising',
  'done',
  'blocked',
  'cancelled',
]

export function taskStatusLabel(task: Task, getName: (id: string) => string): string {
  if (isUserPaused(task)) return PAUSED_LABEL
  if (task.status !== 'waiting') return STATUS_LABEL[task.status]
  const waitingDept = task.waitingOn?.dept ? getName(task.waitingOn.dept) : ''
  return waitingDept && waitingDept !== '—'
    ? `รอการตอบกลับจากฝ่าย${waitingDept}`
    : STATUS_LABEL.waiting
}

export function originLabel(o: Originator, getName: (id: string) => string): string {
  switch (o.kind) {
    case 'user':
      return 'ผู้ใช้'
    case 'executive':
      return 'ผู้บริหาร AI'
    case 'department':
      return `ฝ่าย${getName(o.id)}`
  }
}
