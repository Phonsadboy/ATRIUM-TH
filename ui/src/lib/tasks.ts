import type { Originator, TaskStatus } from '../contract/types'

export const STATUS_LABEL: Record<TaskStatus, string> = {
  backlog: 'รอคิว',
  assigned: 'มอบหมายแล้ว',
  in_progress: 'กำลังทำ',
  review: 'ตรวจงาน',
  revising: 'แก้ตามรีวิว',
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
  done: '#7bbf8f',
  blocked: '#f0735f',
  cancelled: '#9a958c',
}

/** Columns for the kanban board, in flow order. */
export const BOARD_COLUMNS: TaskStatus[] = [
  'assigned',
  'in_progress',
  'review',
  'revising',
  'done',
  'blocked',
  'cancelled',
]

export function originLabel(o: Originator, getName: (id: string) => string): string {
  switch (o.kind) {
    case 'user':
      return 'ผู้ใช้'
    case 'executive':
      return 'ผู้บริหาร'
    case 'department':
      return `ฝ่าย${getName(o.id)}`
  }
}
