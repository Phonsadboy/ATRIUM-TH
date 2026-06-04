import { useMemo } from 'react'
import { useSelector } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Avatar, Dot, Progress, withAlpha } from '../components/primitives'
import { STATUS_HEX, STATUS_LABEL, taskStatusLabel } from '../lib/tasks'
import { threadIdFor } from '../lib/threads'
import { ACCENT_HEX, SEVERITY_HEX, STATE_HEX, STATE_LABEL } from '../lib/visuals'
import type { ActivityEvent, ChatMessage, ChatToolRun, Department, ExecutiveQueueItem, Task, TaskStatus } from '../contract/types'

const OPEN_STATUSES = new Set<TaskStatus>(['backlog', 'assigned', 'in_progress', 'review', 'revising', 'waiting', 'blocked'])
const TASK_RANK: Record<TaskStatus, number> = {
  blocked: 0,
  waiting: 1,
  in_progress: 2,
  review: 3,
  revising: 4,
  assigned: 5,
  backlog: 6,
  done: 7,
  cancelled: 8,
}

function timeLabel(ms?: number | null): string {
  if (!ms) return 'ยังไม่มีสัญญาณ'
  return new Date(ms).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })
}

function shortText(value?: string | null, limit = 170): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text
}

function latestMessage(messages: ChatMessage[]): ChatMessage | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.text?.trim() || msg.pending || msg.streaming || msg.toolRuns?.length) return msg
  }
  return null
}

function runningToolRuns(messages: ChatMessage[]): ChatToolRun[] {
  return messages
    .flatMap((message) => message.toolRuns ?? [])
    .filter((run) => run.status === 'running' || run.status === 'queued')
}

function activeReply(messages: ChatMessage[]): ChatMessage | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.pending || msg.streaming) return msg
  }
  return null
}

function currentOrOpenTask(dept: Department, tasks: Task[]): Task | null {
  if (dept.currentTaskId) {
    const current = tasks.find((task) => task.id === dept.currentTaskId)
    if (current) return current
  }
  return (
    tasks
      .filter((task) => task.departmentId === dept.id && OPEN_STATUSES.has(task.status))
      .slice()
      .sort((a, b) => TASK_RANK[a.status] - TASK_RANK[b.status] || b.updatedAt - a.updatedAt)[0] ?? null
  )
}

function statusSummary(
  dept: Department,
  task: Task | null,
  tools: ChatToolRun[],
  busy: ChatMessage | null,
  getName: (id: string) => string = () => '—',
): {
  label: string
  color: string
  attention: boolean
} {
  if (tools.length) {
    return { label: `กำลังใช้ ${tools.slice(0, 2).map((run) => run.tool).join(', ')}`, color: ACCENT_HEX.lavender, attention: true }
  }
  if (busy) return { label: busy.streaming ? 'กำลังตอบแบบสด' : 'อยู่ในคิวตอบกลับ', color: ACCENT_HEX.sky, attention: true }
  if (dept.state !== 'idle') return { label: STATE_LABEL[dept.state], color: STATE_HEX[dept.state], attention: dept.state !== 'review' }
  if (!task) return { label: 'ไม่มีงานเปิด', color: STATE_HEX.idle, attention: false }
  if (task.status === 'review') return { label: 'รอผู้บริหารตรวจ', color: STATUS_HEX.review, attention: true }
  if (task.status === 'waiting') return { label: taskStatusLabel(task, getName), color: STATUS_HEX.waiting, attention: false }
  if (task.status === 'blocked') return { label: 'งานติดปัญหา', color: STATUS_HEX.blocked, attention: true }
  if (task.status === 'assigned' || task.status === 'backlog' || task.status === 'revising') {
    return { label: 'งานเปิดแต่ยังไม่ประมวลผล', color: ACCENT_HEX.honey, attention: true }
  }
  return { label: STATUS_LABEL[task.status], color: STATUS_HEX[task.status], attention: task.status === 'in_progress' }
}

function signalAge(task: Task | null, message: ChatMessage | null, activity: ActivityEvent | null): number {
  return Math.max(task?.updatedAt ?? 0, message?.ts ?? 0, activity?.ts ?? 0)
}

function queueStatus(item: ExecutiveQueueItem): { label: string; color: string } {
  if (item.status === 'running') return { label: 'กำลังทำ', color: ACCENT_HEX.teal }
  if (item.runAfter > Date.now()) return { label: 'นัดไว้', color: ACCENT_HEX.lavender }
  if (item.lastError) return { label: 'รอลองใหม่', color: ACCENT_HEX.coral }
  return { label: 'รอคิว', color: ACCENT_HEX.amber }
}

function ExecutiveQueueCard({
  queue,
  departments,
  now,
}: {
  queue: ExecutiveQueueItem[]
  departments: Department[]
  now: number
}) {
  const visible = queue.slice(0, 5)
  const running = queue.filter((item) => item.status === 'running').length
  const queued = queue.filter((item) => item.status === 'queued').length
  const deptName = (id?: string | null) => departments.find((dept) => dept.id === id)?.name ?? (id === 'exec' ? 'ผู้บริหาร' : id)

  return (
    <div className="mb-2 rounded-lg border px-3 py-2 text-[12px]" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}>
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-[var(--color-cream)]">คิวประมวลผล</span>
        <span className="text-[var(--color-cream-faint)]">{running} ทำอยู่ · {queued} รอ</span>
      </div>
      {visible.length === 0 ? (
        <div className="mt-1 text-[11px] text-[var(--color-cream-faint)]">คิวว่าง</div>
      ) : (
        <div className="mt-2 space-y-1.5">
          {visible.map((item) => {
            const status = queueStatus(item)
            const waitMs = item.runAfter - now
            return (
              <div key={item.id} className="grid grid-cols-[auto_1fr_auto] items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 rounded-full" style={{ background: status.color }} />
                <div className="min-w-0">
                  <div className="truncate text-[11px] font-medium text-[var(--color-cream)]">{item.title}</div>
                  <div className="truncate text-[10px] text-[var(--color-cream-faint)]">
                    {deptName(item.departmentId)}{item.detail ? ` · ${shortText(item.detail, 80)}` : ''}
                  </div>
                </div>
                <span className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px]" style={{ color: status.color, background: withAlpha(status.color, 0.12) }}>
                  {waitMs > 1_000 && item.status === 'queued' ? timeLabel(item.runAfter) : status.label}
                </span>
              </div>
            )
          })}
          {queue.length > visible.length && (
            <div className="text-[10px] text-[var(--color-cream-faint)]">+{queue.length - visible.length} รายการ</div>
          )}
        </div>
      )}
    </div>
  )
}

function DepartmentWorkCard({
  dept,
  task,
  messages,
  activity,
  now,
}: {
  dept: Department
  task: Task | null
  messages: ChatMessage[]
  activity: ActivityEvent | null
  now: number
}) {
  const departments = useSelector((s) => s.departments)
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)
  const msg = latestMessage(messages)
  const tools = runningToolRuns(messages)
  const busy = activeReply(messages)
  const deptName = (id: string) => departments.find((item) => item.id === id)?.name ?? (id === 'exec' ? 'ผู้บริหาร' : id)
  const status = statusSummary(dept, task, tools, busy, deptName)
  const latestTs = signalAge(task, msg, activity)
  const stale = Boolean(task && task.status !== 'waiting' && OPEN_STATUSES.has(task.status) && latestTs && now - latestTs > 5 * 60_000)
  const lastLog = task?.log?.[task.log.length - 1]
  const accent = ACCENT_HEX[dept.accent]
  const openDept = (tab: 'chat' | 'tasks' | 'memory') => {
    select(dept.id)
    setRightTab(tab)
  }
  const snippet = msg?.role === 'system'
    ? activity?.text || msg.text
    : msg?.text
  const severityColor = activity ? SEVERITY_HEX[activity.severity] : status.color

  return (
    <div
      data-testid={`dept-work-card-${dept.id}`}
      className="rounded-lg border px-3 py-2.5"
      style={{
        borderColor: stale ? withAlpha(ACCENT_HEX.coral, 0.42) : withAlpha(status.color, status.attention ? 0.34 : 0.18),
        background: stale ? withAlpha(ACCENT_HEX.coral, 0.08) : 'var(--color-surface)',
      }}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <Avatar emoji={dept.emoji} accent={dept.accent} state={dept.state} size={34} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13px] font-semibold text-[var(--color-cream)]">ฝ่าย{dept.name}</span>
            <span
              className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
              style={{ background: withAlpha(status.color, 0.13), color: status.color }}
            >
              {status.label}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--color-cream-faint)]">
            <Dot color={STATE_HEX[dept.state]} size={6} pulse={status.attention} />
            <span>{dept.agentName}</span>
            <span>·</span>
            <span>{timeLabel(latestTs)}</span>
            {stale && <span style={{ color: ACCENT_HEX.coral }}>· สัญญาณเงียบเกิน 5 นาที</span>}
          </div>
        </div>
      </div>

      {task && (
        <div className="mt-2 rounded-lg px-2.5 py-2" style={{ background: 'rgba(0,0,0,0.16)' }}>
          <div className="flex min-w-0 items-center gap-2 text-[12px]">
            <span className="min-w-0 flex-1 truncate text-[var(--color-cream)]">{task.title}</span>
            <span className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px]" style={{ color: STATUS_HEX[task.status], background: withAlpha(STATUS_HEX[task.status], 0.12) }}>
              {taskStatusLabel(task, deptName)}
            </span>
          </div>
          <Progress value={Math.max(0, Math.min(1, task.progress ?? 0))} color={accent} className="mt-2" />
          {lastLog && <div className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-cream-dim)]">{shortText(lastLog, 190)}</div>}
        </div>
      )}

      {(snippet || activity) && (
        <div className="mt-2 grid grid-cols-[auto_1fr_auto] items-start gap-2 text-[11px]">
          <span className="mt-1.5 h-1.5 w-1.5 rounded-full" style={{ background: severityColor }} />
          <span className="min-w-0 leading-relaxed text-[var(--color-cream-dim)]">{shortText(snippet || activity?.text, 210)}</span>
          <span className="shrink-0 text-[var(--color-cream-faint)]">{timeLabel(msg?.ts ?? activity?.ts)}</span>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button data-testid={`open-chat-${dept.id}`} type="button" onClick={() => openDept('chat')} className="rounded-md border px-2 py-1 text-[11px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]" style={{ borderColor: withAlpha(accent, 0.32) }}>
          เปิดแชท
        </button>
        <button data-testid={`open-tasks-${dept.id}`} type="button" onClick={() => openDept('tasks')} className="rounded-md border px-2 py-1 text-[11px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]" style={{ borderColor: 'var(--color-line-soft)' }}>
          งาน
        </button>
        <button data-testid={`open-memory-${dept.id}`} type="button" onClick={() => openDept('memory')} className="rounded-md border px-2 py-1 text-[11px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]" style={{ borderColor: 'var(--color-line-soft)' }}>
          ความจำ
        </button>
      </div>
    </div>
  )
}

export function ExecutiveWorkMonitor() {
  const { departments, tasks, threads, activity, now, executiveQueue } = useSelector((s) => ({
    departments: s.departments.filter((dept) => dept.id !== 'exec'),
    tasks: s.tasks,
    threads: s.threads,
    activity: s.activity,
    now: s.now,
    executiveQueue: s.executiveQueue,
  }))

  const items = useMemo(() => {
    return departments
      .map((dept) => {
        const messages = threads[threadIdFor(dept.id)] ?? []
        const task = currentOrOpenTask(dept, tasks)
        const latestActivity = activity.find((item) => item.departmentId === dept.id) ?? null
        const msg = latestMessage(messages)
        const tools = runningToolRuns(messages)
        const busy = activeReply(messages)
        const deptName = (id: string) => departments.find((item) => item.id === id)?.name ?? (id === 'exec' ? 'ผู้บริหาร' : id)
        const status = statusSummary(dept, task, tools, busy, deptName)
        const lastTs = signalAge(task, msg, latestActivity)
        const stale = task && task.status !== 'waiting' && OPEN_STATUSES.has(task.status) && lastTs && now - lastTs > 5 * 60_000
        const rank = stale ? 0 : status.attention ? 1 : task ? 2 : 3
        return { dept, task, messages, latestActivity, rank, lastTs }
      })
      .sort((a, b) => a.rank - b.rank || b.lastTs - a.lastTs || a.dept.name.localeCompare(b.dept.name))
  }, [activity, departments, now, tasks, threads])

  const activeCount = items.filter((item) => item.rank <= 1).length
  const openTaskCount = tasks.filter((task) => task.departmentId && OPEN_STATUSES.has(task.status)).length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2 rounded-lg border px-3 py-2 text-[12px]" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}>
        <div className="flex items-center justify-between gap-3">
          <span className="font-medium text-[var(--color-cream)]">ภาพรวมงานแผนก</span>
          <span className="text-[var(--color-cream-faint)]">{activeCount} active · {openTaskCount} งานเปิด</span>
        </div>
        <div className="mt-1 leading-relaxed text-[var(--color-cream-faint)]">สัญญาณล่าสุดจากทุกห้องแผนก</div>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-x-hidden overflow-y-auto pr-1">
        <ExecutiveQueueCard queue={executiveQueue} departments={departments} now={now} />
        {items.length === 0 && (
          <div className="mt-8 text-center text-sm text-[var(--color-cream-faint)]">ยังไม่มีแผนกให้ติดตาม</div>
        )}
        {items.map((item) => (
          <DepartmentWorkCard
            key={item.dept.id}
            dept={item.dept}
            task={item.task}
            messages={item.messages}
            activity={item.latestActivity}
            now={now}
          />
        ))}
      </div>
    </div>
  )
}
