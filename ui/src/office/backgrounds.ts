/* ============================================================
   ATRIUM — office room backgrounds (image catalogue)

   The only thing the new office reuses from the old build is the
   artwork itself. This module is just a typed catalogue mapping a
   stable id → a room background PNG, plus a couple of guards the
   store needs. No rendering logic lives here.
   ============================================================ */

export const OFFICE_BACKGROUNDS = [
  { id: 'night-ops-lab', name: 'Ops Lab กลางคืน', path: '/art/backgrounds/night-ops-lab.png' },
  { id: 'shared-office-main', name: 'ออฟฟิศกลาง', path: '/art/backgrounds/shared-office-main.png' },
  { id: 'maker-workshop', name: 'Maker Workshop', path: '/art/backgrounds/maker-workshop.png' },
  { id: 'executive-sky-lounge', name: 'ห้องผู้บริหารวิวเมือง', path: '/art/backgrounds/executive-sky-lounge.png' },
  { id: 'creative-studio', name: 'สตูดิโอครีเอทีฟ', path: '/art/backgrounds/creative-studio.png' },
  { id: 'garden-office', name: 'ห้องสวน', path: '/art/backgrounds/garden-office.png' },
] as const

export type OfficeBackgroundId = (typeof OFFICE_BACKGROUNDS)[number]['id']

export const DEFAULT_OFFICE_BACKGROUND_ID: OfficeBackgroundId = 'shared-office-main'

const BY_ID = Object.fromEntries(OFFICE_BACKGROUNDS.map((b) => [b.id, b])) as Record<
  OfficeBackgroundId,
  (typeof OFFICE_BACKGROUNDS)[number]
>

export function sanitizeOfficeBackgroundId(value: unknown): OfficeBackgroundId {
  return typeof value === 'string' && value in BY_ID
    ? (value as OfficeBackgroundId)
    : DEFAULT_OFFICE_BACKGROUND_ID
}

export function backgroundPath(id: OfficeBackgroundId): string {
  return (BY_ID[id] ?? BY_ID[DEFAULT_OFFICE_BACKGROUND_ID]).path
}

/** A stable per-room default so adjacent rooms don't all share one backdrop. */
export function defaultBackgroundForRoom(roomIndex: number): OfficeBackgroundId {
  if (roomIndex === 0) return 'executive-sky-lounge'
  return OFFICE_BACKGROUNDS[roomIndex % OFFICE_BACKGROUNDS.length].id
}
