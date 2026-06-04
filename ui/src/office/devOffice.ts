/* ============================================================
   ATRIUM — DEV-only office playground

   Lets the office canvas (characters, walking, meetings, bubbles) be
   exercised without a live company: seed a believable floor, then fire
   real inputs — pulses, threads — so the same code paths that production
   uses light up. Exposed on `window.__atriumDev` in dev builds only.

   Open the console and try:
     __atriumDev.seed()         // populate a demo office
     __atriumDev.handoff()      // someone walks a task to another desk
     __atriumDev.meeting()      // a few gather at the board
     __atriumDev.endMeeting()
     __atriumDev.say('dept-eng', 'กำลังรีวิว PR อยู่ครับ')
     __atriumDev.coffee()       // someone strolls to the watercooler
     __atriumDev.clear()        // hand the store back to the backend
   ============================================================ */

import type { ApiClient } from '../contract/ApiClient'
import type {
  AccentName,
  AgentState,
  ChatMessage,
  ChatRole,
  CompanyState,
  Department,
  Task,
} from '../contract/types'
import { EXEC_ID, threadIdFor } from '../lib/threads'
import type { Actors } from './actors'
import { setTintHour } from './render'
import type { WorldModel } from './world'

let seq = 0
const uid = (p: string) => `${p}-${(seq += 1)}`
const nowMs = () => Date.now()

function dept(
  id: string,
  name: string,
  role: string,
  emoji: string,
  accent: AccentName,
  state: AgentState,
  mood: number,
): Department {
  return {
    id,
    name,
    role,
    charter: `${name} — ${role}`,
    emoji,
    accent,
    providerId: 'anthropic',
    model: 'claude-opus-4-8',
    thinkingEffort: 'medium',
    speed: 'standard',
    agentName: name,
    state,
    mood,
    currentTaskId: null,
    autonomy: true,
    createdAt: nowMs(),
    room: { x: 0, y: 0, w: 1, h: 1 },
    memory: { archiveChunks: 0, ragEntries: 0, graphNodes: 0, graphEdges: 0, lastCompactionAt: null, tokensSaved: 0 },
    skills: [],
    tools: [],
  }
}

function task(title: string, departmentId: string, status: Task['status']): Task {
  const t = nowMs()
  return {
    id: uid('task'),
    title,
    detail: '',
    status,
    priority: 'normal',
    departmentId,
    origin: { kind: 'user' },
    progress: 0.3,
    createdAt: t,
    updatedAt: t,
    handoffs: [],
    log: [],
  }
}

const DEPTS: Department[] = [
  dept(EXEC_ID, 'ออตโต้ (ผู้บริหาร)', 'Executive', '🎩', 'amber', 'thinking', 0.8),
  dept('dept-eng', 'ทีมวิศวกรรม', 'Engineering', '🛠️', 'teal', 'working', 0.7),
  dept('dept-design', 'ทีมออกแบบ', 'Design', '🎨', 'coral', 'review', 0.75),
  dept('dept-research', 'ทีมวิจัย', 'Research', '🔬', 'lavender', 'thinking', 0.6),
  dept('dept-ops', 'ทีมปฏิบัติการ', 'Ops', '📦', 'sky', 'idle', 0.65),
  dept('dept-sales', 'ทีมขาย', 'Sales', '📈', 'honey', 'working', 0.7),
  dept('dept-qa', 'ทีม QA', 'Quality', '✅', 'teal', 'blocked', 0.45),
]

function buildSeed(): CompanyState {
  return {
    companyName: 'ATRIUM (เดโม)',
    now: nowMs(),
    running: true,
    departments: DEPTS.map((d) => ({ ...d, createdAt: nowMs() })),
    tasks: [
      task('รีแฟกเตอร์ตัวเรนเดอร์', 'dept-eng', 'in_progress'),
      task('ปรับสีปุ่ม', 'dept-design', 'review'),
      task('สรุปผลทดสอบ', 'dept-qa', 'blocked'),
      task('ทำสไลด์นำเสนอ', 'dept-sales', 'assigned'),
    ],
    threads: {},
    activity: [],
    approvals: [],
    objectives: [],
    executiveQueue: [],
    budget: { dailyCapUsd: 500, spentTodayUsd: 12.4 },
  }
}

function msg(deptId: string, text: string, role: ChatRole): ChatMessage {
  return {
    id: uid('msg'),
    threadId: threadIdFor(deptId),
    role,
    authorName: deptId,
    text,
    ts: nowMs(),
    departmentId: deptId,
  }
}

export interface DevOfficeDeps {
  client: ApiClient
  actors: Actors
  getWorld: () => WorldModel
}

export interface DevOfficeApi {
  seed: () => void
  clear: () => void
  handoff: (fromId?: string, toId?: string) => void
  meeting: (ids?: string[]) => void
  endMeeting: () => void
  say: (deptId: string, text: string) => void
  coffee: (deptId?: string) => void
  setState: (deptId: string, state: AgentState) => void
  /** force the time-of-day wash (0–23), or null to follow the clock */
  tint: (hour: number | null) => void
  /** the live actor layer, for console inspection */
  _actors: Actors
}

export function installDevOffice(deps: DevOfficeDeps): () => void {
  const { client, actors, getWorld } = deps

  const ensureSeed = () => {
    if (!client.getState().departments.length) client.devReplaceState(buildSeed())
  }
  const deptIds = () => client.getState().departments.map((d) => d.id)
  const pick = (exclude?: string) => {
    const ids = deptIds().filter((id) => id !== exclude)
    return ids.length ? ids[Math.floor(((seq += 1) * 0.6180339887) % 1 * ids.length)] : undefined
  }

  const api: DevOfficeApi = {
    seed: () => client.devReplaceState(buildSeed()),
    clear: () => client.devRelease(),

    handoff: (fromId, toId) => {
      ensureSeed()
      const from = fromId ?? pick()
      const to = toId ?? pick(from)
      if (from && to) client.devEmitPulse({ kind: 'handoff', departmentId: from, toDepartmentId: to })
    },

    meeting: (ids) => {
      ensureSeed()
      const all = deptIds()
      const want = (ids && ids.length ? ids : [EXEC_ID, ...all.filter((id) => id !== EXEC_ID).slice(0, 3)]).filter((id) =>
        all.includes(id),
      )
      const state = client.getState()
      const messages = want.map((id, i) =>
        msg(id, ['เริ่มประชุมกันเลย', 'ผมเห็นด้วยกับแผนนี้', 'ขอเสริมเรื่อง timeline', 'รับทราบครับ'][i % 4], id === EXEC_ID ? 'executive' : 'agent'),
      )
      client.devReplaceState({
        ...state,
        now: nowMs(),
        threads: { ...state.threads, 'meet:demo': messages },
      })
    },

    endMeeting: () => {
      const state = client.getState()
      const threads = { ...state.threads }
      delete threads['meet:demo']
      client.devReplaceState({ ...state, now: nowMs(), threads })
      actors.endMeeting()
    },

    say: (deptId, text) => {
      ensureSeed()
      const state = client.getState()
      const tid = threadIdFor(deptId)
      const thread = [...(state.threads[tid] ?? []), msg(deptId, text, deptId === EXEC_ID ? 'executive' : 'agent')]
      client.devReplaceState({ ...state, now: nowMs(), threads: { ...state.threads, [tid]: thread } })
    },

    coffee: (deptId) => {
      ensureSeed()
      const world = getWorld()
      const id = deptId ?? pick()
      if (!id) return
      const roomIndex = world.roomByDept.get(id) ?? 0
      const spot = world.rooms[roomIndex]?.coolerSpot
      if (spot) actors.coffee(id, spot)
    },

    setState: (deptId, state) => {
      ensureSeed()
      const s = client.getState()
      client.devReplaceState({
        ...s,
        now: nowMs(),
        departments: s.departments.map((d) => (d.id === deptId ? { ...d, state } : d)),
      })
    },

    tint: (hour) => setTintHour(hour),

    _actors: actors,
  }

  ;(window as unknown as { __atriumDev?: DevOfficeApi }).__atriumDev = api
  console.info('[ATRIUM] dev office ready — try __atriumDev.seed(), .handoff(), .meeting(), .say(id, text)')
  return () => {
    const w = window as unknown as { __atriumDev?: DevOfficeApi }
    if (w.__atriumDev === api) delete w.__atriumDev
  }
}
