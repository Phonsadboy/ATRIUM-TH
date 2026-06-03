// Single source of truth for the operations Console's sections. The shell nav,
// the content header, and the Overview launcher tiles all read from here, so a
// section's emoji / accent / label / description stay consistent everywhere.
import type { ConsoleSection } from '../../state/ui'
import type { AccentName } from '../../contract/types'

export interface SectionMeta {
  id: ConsoleSection
  /** short nav label (Thai) */
  label: string
  /** one-line description shown in the header + launcher tiles */
  desc: string
  /** glyph — emoji read far better than the 18 pixel icons at this scale */
  glyph: string
  accent: AccentName
  /** which attention counter (if any) feeds this item's nav badge */
  badge?: 'projectReviews' | 'warRooms' | 'toolApprovals' | 'orgPlans' | 'debt'
}

export interface SectionGroup {
  id: string
  label: string
  items: SectionMeta[]
}

/** The home dashboard — kept out of the groups so it can sit on its own. */
export const OVERVIEW: SectionMeta = {
  id: 'overview',
  label: 'ภาพรวม',
  desc: 'สรุปสถานะบริษัทและสิ่งที่รอคุณตัดสินใจ',
  glyph: '🛰️',
  accent: 'amber',
}

export const SECTION_GROUPS: SectionGroup[] = [
  {
    id: 'work',
    label: 'งานและผลงาน',
    items: [
      { id: 'projects', label: 'โปรเจกต์', desc: 'งานหลายแผนกที่ตรวจสองชั้น (แผนก → คุณ)', glyph: '📋', accent: 'teal', badge: 'projectReviews' },
      { id: 'artifacts', label: 'ชิ้นงาน', desc: 'ผลงานส่งมอบ เก็บเวอร์ชันและประวัติการแก้', glyph: '📦', accent: 'sky' },
      { id: 'tools', label: 'เครื่องมือ', desc: 'คลังเครื่องมือ ประวัติการเรียกใช้ และคำขออนุมัติ', glyph: '🧰', accent: 'lavender', badge: 'toolApprovals' },
    ],
  },
  {
    id: 'team',
    label: 'ทีมและการประชุม',
    items: [
      { id: 'meetings', label: 'การประชุม', desc: 'นัดประชุม วาระ ผู้เข้าร่วม และบันทึกมติ', glyph: '🗓️', accent: 'sky' },
      { id: 'warrooms', label: 'วอร์รูม', desc: 'ห้องแก้ปัญหาเร่งด่วนแบบรวมศูนย์', glyph: '🚨', accent: 'coral', badge: 'warRooms' },
      { id: 'bulletins', label: 'ประกาศ', desc: 'กระดานประกาศถึงทุกแผนกทั้งบริษัท', glyph: '📣', accent: 'honey' },
    ],
  },
  {
    id: 'knowledge',
    label: 'ความรู้และบทเรียน',
    items: [
      { id: 'memory', label: 'คลังความจำ', desc: 'ความจำห้าชั้น ตรวจที่มาและความเชื่อมั่น', glyph: '🧠', accent: 'lavender' },
      { id: 'playbooks', label: 'คู่มือปฏิบัติ', desc: 'ขั้นตอนการทำงานที่นำกลับมาใช้ซ้ำได้', glyph: '📖', accent: 'teal' },
      { id: 'lessons', label: 'บทเรียน', desc: 'บทเรียนที่สรุปจากความผิดพลาดที่ผ่านมา', glyph: '🎓', accent: 'amber' },
      { id: 'skills', label: 'ทักษะ', desc: 'ทักษะและความสามารถที่เอเจนต์เรียกใช้ได้', glyph: '⚡', accent: 'honey' },
      { id: 'debt', label: 'หนี้ความรู้', desc: 'ช่องว่างความรู้ที่ค้างและควรตามเก็บ', glyph: '📉', accent: 'coral', badge: 'debt' },
    ],
  },
  {
    id: 'automation',
    label: 'ระบบอัตโนมัติ',
    items: [
      { id: 'triggers', label: 'ตารางเวลา', desc: 'ทริกเกอร์และตารางเวลางานอัตโนมัติ', glyph: '⏰', accent: 'amber' },
      { id: 'connectors', label: 'ตัวเชื่อมต่อ', desc: 'สถานะการเชื่อมต่อระบบภายนอก', glyph: '🔌', accent: 'teal' },
    ],
  },
  {
    id: 'governance',
    label: 'กำกับและตรวจสอบ',
    items: [
      { id: 'audit', label: 'บันทึกตรวจสอบ', desc: 'ร่องรอยการกระทำทั้งหมดเพื่อตรวจสอบย้อนหลัง', glyph: '🛡️', accent: 'sky' },
      { id: 'config', label: 'การเปลี่ยนค่าระบบ', desc: 'ประวัติการเปลี่ยนค่าระบบที่ย้อนกลับได้', glyph: '⚙️', accent: 'lavender' },
    ],
  },
  {
    id: 'setup',
    label: 'ตั้งค่าบริษัท',
    items: [
      { id: 'onboarding', label: 'ตั้งบริษัท', desc: 'จัดโครงสร้างองค์กรและตั้งค่าผู้ให้บริการ AI', glyph: '🏛️', accent: 'amber', badge: 'orgPlans' },
      { id: 'owner', label: 'โปรไฟล์เจ้าของ', desc: 'ความชอบและสไตล์การทำงานของคุณ', glyph: '🧑‍💼', accent: 'teal' },
      { id: 'executive', label: 'ผู้บริหาร', desc: 'เอเจนต์ผู้บริหารสูงสุดที่กำกับทั้งบริษัท', glyph: '👔', accent: 'lavender' },
      { id: 'notifyPrefs', label: 'ตั้งค่าแจ้งเตือน', desc: 'เลือกช่องทางและกฎการแจ้งเตือน', glyph: '🔔', accent: 'honey' },
    ],
  },
]

/** Flat lookup: every section (incl. overview) by id. */
export const SECTION_BY_ID: Record<ConsoleSection, SectionMeta> = (() => {
  const map = { [OVERVIEW.id]: OVERVIEW } as Record<ConsoleSection, SectionMeta>
  for (const g of SECTION_GROUPS) for (const it of g.items) map[it.id] = it
  return map
})()
