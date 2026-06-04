import { useSelector, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import { TaskCard } from '../components/TaskCard'
import { ACCENT_HEX } from '../lib/visuals'
import { withAlpha } from '../components/primitives'
import type { Task, TaskStatus } from '../contract/types'

const RANK: Record<TaskStatus, number> = {
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

export function DeptTasks({ deptId }: { deptId: string | null }) {
  const tasks = useSelector(
    (s) =>
      (deptId ? s.tasks.filter((t) => t.departmentId === deptId) : s.tasks)
        .slice()
        .sort((a, b) => RANK[a.status] - RANK[b.status] || b.updatedAt - a.updatedAt),
    shallowArrayEqual,
  )
  const openAssign = useUI((s) => s.openAssign)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <button
        type="button"
        onClick={() => openAssign(deptId)}
        className="mb-3 w-full rounded-xl border border-dashed py-2 text-sm font-medium transition-colors"
        style={{ borderColor: withAlpha(ACCENT_HEX.amber, 0.4), color: ACCENT_HEX.amber }}
      >
        + มอบงานใหม่
      </button>
      <div className="min-h-0 flex-1 space-y-2 overflow-x-hidden overflow-y-auto pr-1">
        {tasks.length === 0 && (
          <div className="mt-8 text-center text-sm text-[var(--color-cream-faint)]">
            ยังไม่มีงาน
          </div>
        )}
        {tasks.map((t: Task) => (
          <TaskCard key={t.id} task={t} />
        ))}
      </div>
    </div>
  )
}
