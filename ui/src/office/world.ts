/* ============================================================
   ATRIUM — office world model (single source of truth)

   The office is ONE continuous, scrollable floor. Departments are
   bucketed into rooms of at most ROOM_CAPACITY (the executive is
   pinned to its room and is free of the cap). Rooms are tiled into a
   grid of cells with corridors between them; inside each room up to
   ten departments sit on a 5×2 desk grid.

   Everything here is pure: given the store's data it returns world-
   space geometry in CSS pixels. The canvas renderer and the React
   host both read from this so they can never drift.
   ============================================================ */

import type { AccentName, AgentState, Department, Task } from '../contract/types'
import { EXEC_ID } from '../lib/threads'
import {
  defaultBackgroundForRoom,
  sanitizeOfficeBackgroundId,
  type OfficeBackgroundId,
} from './backgrounds'
import { charPath, chairPath, deskPath, DECOR, type CharPose } from './assets'

export const ROOM_CAPACITY = 10

/* ---- world-space dimensions (CSS px at zoom 1) ---- */
export const ROOM_W = 900
export const ROOM_H = 520
export const ROOM_GAP = 110 // corridor between rooms
export const WORLD_PAD = 120 // breathing room around the whole building
export const HEADER_H = 50 // room title band
export const ROOM_INSET = 26 // wall padding inside a room
const SLOT_COLS = 5
const SLOT_ROWS = 2

const OPEN_TASK_STATUSES = new Set(['backlog', 'assigned', 'in_progress', 'review', 'revising', 'blocked'])

export interface DeptSprite {
  id: string
  name: string
  accent: AccentName
  state: AgentState
  mood: number
  isExec: boolean
  roomIndex: number
  charId: string
  charPose: CharPose
  charSrc: string
  deskSrc: string
  chairSrc: string
  taskCount: number
  taskTitle: string | null
  /** anim phase so identical states don't bob in lock-step */
  phase: number
  /** world-space cell the department occupies (hit target) */
  cell: { x: number; y: number; w: number; h: number }
  /** world-space anchor for the character's feet */
  anchor: { x: number; y: number }
  scale: number
}

/** A static prop drawn behind the desks. (x,y) is the bottom-center anchor
 *  (where it meets the floor) in world space; the renderer draws it at
 *  (x - w/2, y - h). */
export interface DecorItem {
  src: string
  x: number
  y: number
  w: number
  h: number
}

export interface RoomModel {
  index: number
  title: string
  accent: AccentName
  backgroundId: OfficeBackgroundId
  isExec: boolean
  rect: { x: number; y: number; w: number; h: number }
  count: number
  depts: DeptSprite[]
  /** background props (plants, watercooler, whiteboard…) */
  decor: DecorItem[]
  /** where characters gather for a meeting (in front of the board) */
  meetingSpot: { x: number; y: number }
  /** where a character stands to grab coffee */
  coolerSpot: { x: number; y: number }
}

export interface WorldModel {
  rooms: RoomModel[]
  cols: number
  width: number
  height: number
  byId: Map<string, DeptSprite>
  roomByDept: Map<string, number>
}

/* ---------- room grid ---------- */

export function roomColumns(roomCount: number): number {
  if (roomCount <= 1) return 1
  if (roomCount <= 4) return 2
  return 3
}

function roomOrigin(index: number, cols: number): { x: number; y: number } {
  const col = index % cols
  const row = Math.floor(index / cols)
  return {
    x: WORLD_PAD + col * (ROOM_W + ROOM_GAP),
    y: WORLD_PAD + row * (ROOM_H + ROOM_GAP),
  }
}

/** Desk cells inside a room (room 0 also seats the exec → up to 11). */
function slotCells(count: number): { x: number; y: number; w: number; h: number }[] {
  const n = Math.max(1, Math.min(ROOM_CAPACITY + 1, count))
  const cols = n <= 1 ? 1 : n <= 2 ? 2 : n <= 6 ? 3 : n <= 8 ? 4 : SLOT_COLS
  const rows = Math.ceil(n / cols)
  const innerX = ROOM_INSET
  const innerY = HEADER_H + ROOM_INSET
  const innerW = ROOM_W - ROOM_INSET * 2
  const innerH = ROOM_H - HEADER_H - ROOM_INSET * 2
  const cellW = innerW / cols
  // a single-row room only uses half the interior height (keeps desks human-sized)
  const cellH = innerH / Math.max(rows, SLOT_ROWS)
  // vertically center the used rows inside the interior
  const usedH = cellH * rows
  const y0 = innerY + Math.max(0, (innerH - usedH) / 2)

  const cells: { x: number; y: number; w: number; h: number }[] = []
  for (let i = 0; i < n; i++) {
    const row = Math.floor(i / cols)
    const inRow = Math.min(cols, n - row * cols)
    const rowW = cellW * inRow
    const x0 = innerX + (innerW - rowW) / 2
    const col = i % cols
    cells.push({ x: x0 + col * cellW, y: y0 + row * cellH, w: cellW, h: cellH })
  }
  return cells
}

/* ---------- room furniture (decor + gather points) ---------- */

/** Deterministic background props + the meeting / coffee anchor points for a
 *  room at world origin (ox,oy). Props are drawn behind the desks, so in a
 *  packed room they simply peek out from behind — never block a desk. */
function roomFurniture(
  ox: number,
  oy: number,
): { decor: DecorItem[]; meetingSpot: { x: number; y: number }; coolerSpot: { x: number; y: number } } {
  const topY = oy + HEADER_H + 60 // just below the title band
  const botY = oy + ROOM_H - 28 // near the bottom wall
  const L = ox + 52
  const R = ox + ROOM_W - 52
  const midX = ox + ROOM_W / 2
  const meetingSpot = { x: midX, y: oy + ROOM_H - 92 } // open floor below the desks
  const coolerSpot = { x: R, y: topY + 14 }
  const decor: DecorItem[] = [
    { src: DECOR.plantTall, x: L, y: topY, w: 56, h: 80 },
    { src: DECOR.watercooler, x: R, y: topY, w: 52, h: 68 },
    { src: DECOR.whiteboard, x: midX, y: topY - 4, w: 74, h: 58 }, // board on the top wall
    { src: DECOR.meetingTable, x: meetingSpot.x, y: meetingSpot.y + 16, w: 80, h: 56 }, // table they ring around
    { src: DECOR.door, x: ox + ROOM_W * 0.3, y: oy + ROOM_H - 18, w: 54, h: 64 }, // door on the bottom wall
    { src: DECOR.bookshelf, x: L, y: botY, w: 60, h: 68 },
    { src: DECOR.plantSmall, x: R, y: botY, w: 48, h: 56 },
  ]
  return { decor, meetingSpot, coolerSpot }
}

/* ---------- stable per-department assignment ---------- */

function hash(id: string): number {
  let h = 2166136261
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function poseFor(state: AgentState): CharPose {
  if (state === 'working' || state === 'review' || state === 'thinking') return 'sit-work'
  if (state === 'offline') return 'sit-sleep'
  if (state === 'handoff') return 'walk-down'
  return 'sit-idle'
}

/* ---------- task rollup ---------- */

function taskRollup(tasks: Task[]): Map<string, { count: number; title: string | null }> {
  const open = new Map<string, Task[]>()
  for (const t of tasks) {
    if (!t.departmentId || !OPEN_TASK_STATUSES.has(t.status)) continue
    const list = open.get(t.departmentId) ?? []
    list.push(t)
    open.set(t.departmentId, list)
  }
  const out = new Map<string, { count: number; title: string | null }>()
  for (const [deptId, list] of open) {
    list.sort((a, b) => b.updatedAt - a.updatedAt)
    out.set(deptId, { count: list.length, title: list[0]?.title ?? null })
  }
  return out
}

/* ---------- bucketing ---------- */

function bucket(
  departments: Department[],
  departmentRooms: Record<string, number>,
): { rooms: Department[][]; roomByDept: Map<string, number> } {
  const rooms: Department[][] = []
  const counts: number[] = []
  const roomByDept = new Map<string, number>()
  const ensure = (i: number) => {
    while (rooms.length <= i) {
      rooms.push([])
      counts.push(0)
    }
  }

  let order = 0
  for (const d of departments) {
    const explicit = departmentRooms[d.id]
    if (d.id === EXEC_ID) {
      const idx = Number.isFinite(explicit) ? Math.max(0, Math.floor(explicit)) : 0
      ensure(idx)
      rooms[idx].push(d) // exec does NOT consume capacity
      roomByDept.set(d.id, idx)
      continue
    }
    let idx = Number.isFinite(explicit) ? Math.max(0, Math.floor(explicit)) : Math.floor(order / ROOM_CAPACITY)
    order += 1
    ensure(idx)
    while (counts[idx] >= ROOM_CAPACITY) {
      idx += 1
      ensure(idx)
    }
    rooms[idx].push(d)
    counts[idx] += 1
    roomByDept.set(d.id, idx)
  }
  if (!rooms.length) rooms.push([])
  return { rooms, roomByDept }
}

/* ---------- public builder ---------- */

export interface BuildOpts {
  departmentRooms: Record<string, number>
  roomOverrides: Record<string, { x: number; y: number }>
  roomBackgrounds: Record<string, OfficeBackgroundId>
  /** user-dragged room origins, keyed by room index */
  roomPositions: Record<string, { x: number; y: number }>
  /** manual floor for the room count (lets the owner add empty rooms) */
  minRooms: number
}

export function buildWorld(departments: Department[], tasks: Task[], opts: BuildOpts): WorldModel {
  const { departmentRooms, roomOverrides, roomBackgrounds, roomPositions, minRooms } = opts
  const { rooms: buckets, roomByDept } = bucket(departments, departmentRooms)
  // pad with empty rooms so a freshly-added room is visible before anyone moves in
  while (buckets.length < Math.max(1, minRooms)) buckets.push([])
  const tasksByDept = taskRollup(tasks)
  const cols = roomColumns(buckets.length)
  const byId = new Map<string, DeptSprite>()

  const rooms: RoomModel[] = buckets.map((members, index) => {
    const savedPos = roomPositions[String(index)]
    const origin = savedPos ? { x: Math.max(0, savedPos.x), y: Math.max(0, savedPos.y) } : roomOrigin(index, cols)
    const cells = slotCells(members.length)
    const hasExec = members.some((d) => d.id === EXEC_ID)
    const furniture = roomFurniture(origin.x, origin.y)
    const accent: AccentName = hasExec ? 'amber' : (members[0]?.accent ?? 'teal')
    const saved = roomBackgrounds[String(index)]
    const backgroundId: OfficeBackgroundId = saved
      ? sanitizeOfficeBackgroundId(saved)
      : defaultBackgroundForRoom(index)

    const depts: DeptSprite[] = members.map((d, i) => {
      const cell = cells[Math.min(i, cells.length - 1)]
      const h = hash(d.id)
      const isExec = d.id === EXEC_ID
      const charId = isExec ? 'exec' : `char${String((h % 10) + 1).padStart(2, '0')}`
      const pose = poseFor(d.state)
      // override nudges the desk relative to its grid slot (graceful migration
      // from any old saved value — small numbers stay small nudges)
      const ov = roomOverrides[d.id]
      const dx = ov ? ov.x : 0
      const dy = ov ? ov.y : 0
      const cx = origin.x + cell.x + cell.w / 2 + dx
      const top = origin.y + cell.y + dy
      const scale = Math.min(2.5, Math.max(1.6, Math.min(cell.w / 96, cell.h / 110)))
      const sprite: DeptSprite = {
        id: d.id,
        name: d.name,
        accent: d.accent,
        state: d.state,
        mood: d.mood,
        isExec,
        roomIndex: index,
        charId,
        charPose: pose,
        charSrc: charPath(charId, pose),
        deskSrc: deskPath(isExec ? 0 : h % 10),
        chairSrc: chairPath(h % 4),
        taskCount: tasksByDept.get(d.id)?.count ?? 0,
        taskTitle: tasksByDept.get(d.id)?.title ?? null,
        phase: (h % 1000) / 1000,
        cell: { x: origin.x + cell.x + dx, y: top, w: cell.w, h: cell.h },
        anchor: { x: cx, y: top + cell.h * 0.72 },
        scale,
      }
      byId.set(d.id, sprite)
      return sprite
    })

    return {
      index,
      title: hasExec ? 'ห้องผู้บริหาร' : `ห้อง ${index + 1}`,
      accent,
      backgroundId,
      isExec: hasExec,
      rect: { x: origin.x, y: origin.y, w: ROOM_W, h: ROOM_H },
      count: members.filter((d) => d.id !== EXEC_ID).length,
      depts,
      decor: furniture.decor,
      meetingSpot: furniture.meetingSpot,
      coolerSpot: furniture.coolerSpot,
    }
  })

  // world bounds cover every room (grid-placed or dragged) plus a margin
  let maxX = 0
  let maxY = 0
  for (const r of rooms) {
    maxX = Math.max(maxX, r.rect.x + r.rect.w)
    maxY = Math.max(maxY, r.rect.y + r.rect.h)
  }
  const width = maxX + WORLD_PAD
  const height = maxY + WORLD_PAD

  return { rooms, cols, width, height, byId, roomByDept }
}

/** Center of a room in world space (for camera framing). */
export function roomCenter(world: WorldModel, index: number): { x: number; y: number } | null {
  const room = world.rooms[index]
  if (!room) return null
  return { x: room.rect.x + room.rect.w / 2, y: room.rect.y + room.rect.h / 2 }
}
