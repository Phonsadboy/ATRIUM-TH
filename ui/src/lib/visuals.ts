import type { AccentName, AgentState, Priority, Severity } from '../contract/types'

export const ACCENT_HEX: Record<AccentName, string> = {
  amber: '#f4a945',
  teal: '#57d6bf',
  coral: '#f0735f',
  lavender: '#b3a4ee',
  sky: '#7eb0dc',
  honey: '#e8c07a',
}

export const ACCENT_LIST: AccentName[] = [
  'amber',
  'teal',
  'coral',
  'lavender',
  'sky',
  'honey',
]

export function accentSoft(name: AccentName, alpha = 0.14): string {
  const hex = ACCENT_HEX[name]
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export const STATE_LABEL: Record<AgentState, string> = {
  idle: 'ว่าง',
  thinking: 'กำลังคิด',
  working: 'กำลังทำงาน',
  review: 'กำลังตรวจงาน',
  handoff: 'กำลังส่งต่อ',
  blocked: 'ติดปัญหา',
  offline: 'ออฟไลน์',
}

export const STATE_HEX: Record<AgentState, string> = {
  idle: '#aba090',
  thinking: '#7eb0dc',
  working: '#57d6bf',
  review: '#f4a945',
  handoff: '#b3a4ee',
  blocked: '#f0735f',
  offline: '#6f6657',
}

export const PRIORITY_LABEL: Record<Priority, string> = {
  low: 'ต่ำ',
  normal: 'ปกติ',
  high: 'สูง',
  urgent: 'ด่วน',
}

export const PRIORITY_HEX: Record<Priority, string> = {
  low: '#7eb0dc',
  normal: '#aba090',
  high: '#f4a945',
  urgent: '#f0735f',
}

export const SEVERITY_HEX: Record<Severity, string> = {
  info: '#aba090',
  good: '#57d6bf',
  warn: '#f4a945',
  alert: '#f0735f',
}
