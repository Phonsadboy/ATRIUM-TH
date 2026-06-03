import { create } from 'zustand'
import {
  sanitizeOfficeBackgroundId,
  type OfficeBackgroundId,
} from '../office/backgrounds'

export type RightTab = 'chat' | 'tasks' | 'memory'
export type MobileSection = 'depts' | 'office' | 'detail'

/** Sections of the operations Console (the big workspace overlay). */
export type ConsoleSection =
  | 'overview'
  | 'onboarding'
  | 'projects'
  | 'artifacts'
  | 'tools'
  | 'memory'
  | 'config'
  | 'meetings'
  | 'warrooms'
  | 'playbooks'
  | 'lessons'
  | 'triggers'
  | 'bulletins'
  | 'audit'
  | 'connectors'
  | 'skills'
  | 'debt'
  | 'owner'
  | 'executive'
  | 'notifyPrefs'

interface UIState {
  /** null = executive context */
  selectedDeptId: string | null
  rightTab: RightTab
  createDeptOpen: boolean
  assignOpen: boolean
  assignDeptId: string | null
  approvalsOpen: boolean
  notificationsOpen: boolean
  decisionsOpen: boolean
  taskBoardOpen: boolean
  financeOpen: boolean
  /** operations Console overlay */
  consoleOpen: boolean
  consoleSection: ConsoleSection
  /** visible single-column section on narrow screens */
  mobileSection: MobileSection
  /** id of the department being edited, or null when the edit modal is closed */
  editDeptId: string | null
  /** office "arrange" mode — rooms become draggable; off = plain click-to-select */
  arrangeMode: boolean
  /** active office room page on the canvas */
  officeRoomIndex: number
  /** per-department office room assignment, persisted locally */
  departmentRooms: Record<string, number>
  /** per-room background image assignment, persisted locally */
  officeRoomBackgrounds: Record<string, OfficeBackgroundId>
  /** per-department office position overrides (tile coords), persisted locally */
  roomOverrides: Record<string, { x: number; y: number }>
  /** per-room world-position overrides (drag a whole room), keyed by room index */
  roomPositions: Record<string, { x: number; y: number }>
  /** manual floor for how many rooms exist (lets the owner add empty rooms) */
  officeRoomCount: number
  /** left rail (departments) collapsed to a thin strip — desktop only */
  leftCollapsed: boolean
  /** right dock (chat/tasks/memory) collapsed to a thin strip — desktop only */
  dockCollapsed: boolean
  /** right dock width in px (desktop), user-resizable via the divider */
  dockWidth: number

  select: (deptId: string | null) => void
  setRightTab: (tab: RightTab) => void
  setMobileSection: (section: MobileSection) => void
  openCreateDept: () => void
  closeCreateDept: () => void
  openAssign: (deptId?: string | null) => void
  closeAssign: () => void
  toggleApprovals: (open?: boolean) => void
  toggleNotifications: (open?: boolean) => void
  toggleDecisions: (open?: boolean) => void
  toggleTaskBoard: (open?: boolean) => void
  openFinance: () => void
  closeFinance: () => void
  openConsole: (section?: ConsoleSection) => void
  closeConsole: () => void
  setConsoleSection: (section: ConsoleSection) => void
  openEditDept: (deptId: string) => void
  closeEditDept: () => void
  setOfficeRoom: (roomIndex: number) => void
  setOfficeRoomBackground: (roomIndex: number, backgroundId: OfficeBackgroundId) => void
  moveDepartmentToOfficeRoom: (deptId: string, roomIndex: number) => void
  setRoomOverride: (deptId: string, pos: { x: number; y: number }) => void
  setRoomPosition: (roomIndex: number, pos: { x: number; y: number }) => void
  /** add one empty room beyond the current count (pass the live room count) */
  addOfficeRoom: (currentRoomCount?: number) => void
  applyOfficePlan: (plan: OfficeLayoutPlan) => void
  toggleArrange: (on?: boolean) => void
  toggleLeft: (on?: boolean) => void
  toggleDock: (on?: boolean) => void
  /** live width update while dragging (not persisted) */
  setDockWidth: (px: number) => void
  /** persist the current layout (call on drag end) */
  commitDockWidth: () => void
}

const ROOM_KEY = 'atrium.roomOverrides.v2'
const DEPARTMENT_ROOMS_KEY = 'atrium.departmentRooms'
const ACTIVE_OFFICE_ROOM_KEY = 'atrium.officeRoomIndex'
const ROOM_BACKGROUNDS_KEY = 'atrium.officeRoomBackgrounds'
const ROOM_POSITIONS_KEY = 'atrium.officeRoomPositions'
const ROOM_COUNT_KEY = 'atrium.officeRoomCount'

export interface OfficeLayoutPlan {
  activeRoomIndex?: number
  departmentRooms?: Record<string, number>
  roomOverrides?: Record<string, { x: number; y: number }>
  roomBackgrounds?: Record<string | number, OfficeBackgroundId | string>
  roomPositions?: Record<string | number, { x: number; y: number }>
  roomCount?: number
}

function loadRoomPositions(): Record<string, { x: number; y: number }> {
  try {
    const raw = JSON.parse(localStorage.getItem(ROOM_POSITIONS_KEY) || '{}')
    if (!raw || typeof raw !== 'object') return {}
    const next: Record<string, { x: number; y: number }> = {}
    for (const [k, v] of Object.entries(raw)) {
      const pos = sanitizePosition(v)
      if (pos) next[String(sanitizeRoomIndex(Number(k)))] = pos
    }
    return next
  } catch {
    return {}
  }
}

function persistRoomPositions(positions: Record<string, { x: number; y: number }>) {
  try {
    localStorage.setItem(ROOM_POSITIONS_KEY, JSON.stringify(positions))
  } catch {
    /* ignore quota/availability errors */
  }
}

function loadOfficeRoomCount(): number {
  try {
    return sanitizeRoomIndex(JSON.parse(localStorage.getItem(ROOM_COUNT_KEY) || '0'))
  } catch {
    return 0
  }
}

function persistOfficeRoomCount(count: number) {
  try {
    localStorage.setItem(ROOM_COUNT_KEY, JSON.stringify(Math.max(0, Math.floor(count))))
  } catch {
    /* ignore quota/availability errors */
  }
}

function loadRoomOverrides(): Record<string, { x: number; y: number }> {
  try {
    return JSON.parse(localStorage.getItem(ROOM_KEY) || '{}')
  } catch {
    return {}
  }
}

function sanitizeRoomIndex(roomIndex: unknown): number {
  if (typeof roomIndex !== 'number' || !Number.isFinite(roomIndex)) return 0
  return Math.max(0, Math.floor(roomIndex))
}

function sanitizePosition(pos: unknown): { x: number; y: number } | null {
  if (!pos || typeof pos !== 'object') return null
  const { x, y } = pos as { x?: unknown; y?: unknown }
  if (typeof x !== 'number' || typeof y !== 'number' || !Number.isFinite(x) || !Number.isFinite(y)) return null
  return { x, y }
}

function loadDepartmentRooms(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(DEPARTMENT_ROOMS_KEY) || '{}')
    if (!raw || typeof raw !== 'object') return {}
    const next: Record<string, number> = {}
    for (const [deptId, roomIndex] of Object.entries(raw)) next[deptId] = sanitizeRoomIndex(roomIndex)
    return next
  } catch {
    return {}
  }
}

function persistDepartmentRooms(rooms: Record<string, number>) {
  try {
    localStorage.setItem(DEPARTMENT_ROOMS_KEY, JSON.stringify(rooms))
  } catch {
    /* ignore quota/availability errors */
  }
}

function loadOfficeRoomBackgrounds(): Record<string, OfficeBackgroundId> {
  try {
    const raw = JSON.parse(localStorage.getItem(ROOM_BACKGROUNDS_KEY) || '{}')
    if (!raw || typeof raw !== 'object') return {}
    const next: Record<string, OfficeBackgroundId> = {}
    for (const [roomIndex, backgroundId] of Object.entries(raw)) {
      next[String(sanitizeRoomIndex(Number(roomIndex)))] = sanitizeOfficeBackgroundId(backgroundId)
    }
    return next
  } catch {
    return {}
  }
}

function persistOfficeRoomBackgrounds(backgrounds: Record<string, OfficeBackgroundId>) {
  try {
    localStorage.setItem(ROOM_BACKGROUNDS_KEY, JSON.stringify(backgrounds))
  } catch {
    /* ignore quota/availability errors */
  }
}

function loadOfficeRoomIndex(): number {
  try {
    return sanitizeRoomIndex(JSON.parse(localStorage.getItem(ACTIVE_OFFICE_ROOM_KEY) || '0'))
  } catch {
    return 0
  }
}

function persistOfficeRoomIndex(roomIndex: number) {
  try {
    localStorage.setItem(ACTIVE_OFFICE_ROOM_KEY, JSON.stringify(sanitizeRoomIndex(roomIndex)))
  } catch {
    /* ignore quota/availability errors */
  }
}

const LAYOUT_KEY = 'atrium.layout'
const DOCK_MIN = 340
const DOCK_MAX = 900
const DOCK_DEFAULT = 480

function clampDock(px: number): number {
  if (!Number.isFinite(px)) return DOCK_DEFAULT
  return Math.min(DOCK_MAX, Math.max(DOCK_MIN, Math.round(px)))
}

type LayoutPrefs = { leftCollapsed: boolean; dockCollapsed: boolean; dockWidth: number }

function loadLayout(): LayoutPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}')
    return {
      leftCollapsed: !!raw.leftCollapsed,
      dockCollapsed: !!raw.dockCollapsed,
      dockWidth: clampDock(typeof raw.dockWidth === 'number' ? raw.dockWidth : DOCK_DEFAULT),
    }
  } catch {
    return { leftCollapsed: false, dockCollapsed: false, dockWidth: DOCK_DEFAULT }
  }
}

function persistLayout(p: LayoutPrefs) {
  try {
    localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        leftCollapsed: p.leftCollapsed,
        dockCollapsed: p.dockCollapsed,
        dockWidth: p.dockWidth,
      }),
    )
  } catch {
    /* ignore quota/availability errors */
  }
}

const initialLayout = loadLayout()
const initialRoomBackgrounds = loadOfficeRoomBackgrounds()

export const useUI = create<UIState>((set, get) => ({
  selectedDeptId: null,
  arrangeMode: false,
  officeRoomIndex: loadOfficeRoomIndex(),
  departmentRooms: loadDepartmentRooms(),
  officeRoomBackgrounds: initialRoomBackgrounds,
  roomOverrides: loadRoomOverrides(),
  roomPositions: loadRoomPositions(),
  officeRoomCount: loadOfficeRoomCount(),
  leftCollapsed: initialLayout.leftCollapsed,
  dockCollapsed: initialLayout.dockCollapsed,
  dockWidth: initialLayout.dockWidth,
  rightTab: 'chat',
  createDeptOpen: false,
  assignOpen: false,
  assignDeptId: null,
  approvalsOpen: false,
  notificationsOpen: false,
  decisionsOpen: false,
  taskBoardOpen: false,
  financeOpen: false,
  consoleOpen: false,
  consoleSection: 'overview',
  mobileSection: 'detail',
  editDeptId: null,

  select: (deptId) => set({ selectedDeptId: deptId, mobileSection: 'detail' }),
  setRightTab: (tab) => set({ rightTab: tab, mobileSection: 'detail' }),
  setMobileSection: (section) => set({ mobileSection: section }),
  openCreateDept: () => set({ createDeptOpen: true }),
  closeCreateDept: () => set({ createDeptOpen: false }),
  openAssign: (deptId = null) => set({ assignOpen: true, assignDeptId: deptId }),
  closeAssign: () => set({ assignOpen: false }),
  toggleApprovals: (open) =>
    set((s) => ({ approvalsOpen: open ?? !s.approvalsOpen })),
  toggleNotifications: (open) =>
    set((s) => ({ notificationsOpen: open ?? !s.notificationsOpen })),
  toggleDecisions: (open) =>
    set((s) => ({ decisionsOpen: open ?? !s.decisionsOpen })),
  toggleTaskBoard: (open) =>
    set((s) => ({ taskBoardOpen: open ?? !s.taskBoardOpen })),
  openFinance: () => set({ financeOpen: true }),
  closeFinance: () => set({ financeOpen: false }),
  openConsole: (section) =>
    set((s) => ({ consoleOpen: true, consoleSection: section ?? s.consoleSection })),
  closeConsole: () => set({ consoleOpen: false }),
  setConsoleSection: (section) => set({ consoleSection: section }),
  openEditDept: (deptId) => set({ editDeptId: deptId }),
  closeEditDept: () => set({ editDeptId: null }),
  setOfficeRoom: (roomIndex) => {
    const next = sanitizeRoomIndex(roomIndex)
    persistOfficeRoomIndex(next)
    set({ officeRoomIndex: next })
  },
  setOfficeRoomBackground: (roomIndex, backgroundId) =>
    set((s) => {
      const key = String(sanitizeRoomIndex(roomIndex))
      const next = {
        ...s.officeRoomBackgrounds,
        [key]: sanitizeOfficeBackgroundId(backgroundId),
      }
      persistOfficeRoomBackgrounds(next)
      return { officeRoomBackgrounds: next }
    }),
  moveDepartmentToOfficeRoom: (deptId, roomIndex) =>
    set((s) => {
      const nextRoomIndex = sanitizeRoomIndex(roomIndex)
      const nextRooms = { ...s.departmentRooms, [deptId]: nextRoomIndex }
      const nextOverrides = { ...s.roomOverrides }
      delete nextOverrides[deptId]
      persistDepartmentRooms(nextRooms)
      try {
        localStorage.setItem(ROOM_KEY, JSON.stringify(nextOverrides))
      } catch {
        /* ignore quota/availability errors */
      }
      persistOfficeRoomIndex(nextRoomIndex)
      return {
        departmentRooms: nextRooms,
        roomOverrides: nextOverrides,
        officeRoomIndex: nextRoomIndex,
      }
    }),
  setRoomOverride: (deptId, pos) =>
    set((s) => {
      const next = { ...s.roomOverrides, [deptId]: pos }
      try {
        localStorage.setItem(ROOM_KEY, JSON.stringify(next))
      } catch {
        /* ignore quota/availability errors */
      }
      return { roomOverrides: next }
    }),
  setRoomPosition: (roomIndex, pos) =>
    set((s) => {
      const clean = sanitizePosition(pos)
      if (!clean) return s
      const next = { ...s.roomPositions, [String(sanitizeRoomIndex(roomIndex))]: clean }
      persistRoomPositions(next)
      return { roomPositions: next }
    }),
  addOfficeRoom: (currentRoomCount) =>
    set((s) => {
      // one empty room beyond whatever exists now (live count from the world, or
      // a floor derived from explicit assignments when not supplied)
      const used = Object.values(s.departmentRooms)
      const maxUsed = used.length ? Math.max(...used.map((n) => sanitizeRoomIndex(n))) + 1 : 0
      const existing = Math.max(s.officeRoomCount, maxUsed, currentRoomCount ?? 0)
      const next = existing + 1
      persistOfficeRoomCount(next)
      persistOfficeRoomIndex(next - 1)
      return { officeRoomCount: next, officeRoomIndex: next - 1 }
    }),
  applyOfficePlan: (plan) =>
    set((s) => {
      const nextRooms = { ...s.departmentRooms }
      for (const [deptId, roomIndex] of Object.entries(plan.departmentRooms ?? {})) {
        nextRooms[deptId] = sanitizeRoomIndex(roomIndex)
      }

      const nextOverrides = { ...s.roomOverrides }
      for (const deptId of Object.keys(plan.departmentRooms ?? {})) {
        if (!(deptId in (plan.roomOverrides ?? {}))) delete nextOverrides[deptId]
      }
      for (const [deptId, pos] of Object.entries(plan.roomOverrides ?? {})) {
        const clean = sanitizePosition(pos)
        if (clean) nextOverrides[deptId] = clean
      }

      const nextBackgrounds = { ...s.officeRoomBackgrounds }
      for (const [roomIndex, backgroundId] of Object.entries(plan.roomBackgrounds ?? {})) {
        nextBackgrounds[String(sanitizeRoomIndex(Number(roomIndex)))] =
          sanitizeOfficeBackgroundId(backgroundId)
      }

      const nextPositions = { ...s.roomPositions }
      for (const [roomIndex, pos] of Object.entries(plan.roomPositions ?? {})) {
        const clean = sanitizePosition(pos)
        if (clean) nextPositions[String(sanitizeRoomIndex(Number(roomIndex)))] = clean
      }

      const nextRoomCount =
        plan.roomCount === undefined ? s.officeRoomCount : Math.max(0, Math.floor(plan.roomCount))

      const nextOfficeRoomIndex =
        plan.activeRoomIndex === undefined ? s.officeRoomIndex : sanitizeRoomIndex(plan.activeRoomIndex)

      persistDepartmentRooms(nextRooms)
      persistOfficeRoomBackgrounds(nextBackgrounds)
      persistOfficeRoomIndex(nextOfficeRoomIndex)
      persistRoomPositions(nextPositions)
      persistOfficeRoomCount(nextRoomCount)
      try {
        localStorage.setItem(ROOM_KEY, JSON.stringify(nextOverrides))
      } catch {
        /* ignore quota/availability errors */
      }
      return {
        departmentRooms: nextRooms,
        officeRoomBackgrounds: nextBackgrounds,
        roomOverrides: nextOverrides,
        roomPositions: nextPositions,
        officeRoomCount: nextRoomCount,
        officeRoomIndex: nextOfficeRoomIndex,
      }
    }),
  toggleArrange: (on) => set((s) => ({ arrangeMode: on ?? !s.arrangeMode })),
  toggleLeft: (on) =>
    set((s) => {
      const leftCollapsed = on ?? !s.leftCollapsed
      persistLayout({ ...s, leftCollapsed })
      return { leftCollapsed }
    }),
  toggleDock: (on) =>
    set((s) => {
      const dockCollapsed = on ?? !s.dockCollapsed
      persistLayout({ ...s, dockCollapsed })
      return { dockCollapsed }
    }),
  setDockWidth: (px) => set({ dockWidth: clampDock(px) }),
  commitDockWidth: () => persistLayout(get()),
}))
