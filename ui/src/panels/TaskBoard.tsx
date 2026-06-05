import { useSelector, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Modal, withAlpha } from '../components/primitives'
import { TaskCard } from '../components/TaskCard'
import { BOARD_COLUMNS, STATUS_HEX, STATUS_LABEL } from '../lib/tasks'
import type { Task, TaskStatus } from '../contract/types'

export function TaskBoard() {
  const open = useUI((s) => s.taskBoardOpen)
  const toggle = useUI((s) => s.toggleTaskBoard)
  const openAssign = useUI((s) => s.openAssign)
  const tasks = useSelector((s) => s.tasks, shallowArrayEqual)

  const byStatus = (status: TaskStatus) =>
    tasks
      .filter((t) => t.status === status)
      .sort((a, b) => b.updatedAt - a.updatedAt)

  return (
    <Modal
      open={open}
      onClose={() => toggle(false)}
      width={1480}
      title={
        <span className="flex items-center gap-3">
          บอร์ดงานทั้งบริษัท
          <span className="text-[11px] font-normal text-[var(--color-cream-faint)]">
            {tasks.length} งาน
          </span>
          <button
            type="button"
            onClick={() => {
              toggle(false)
              openAssign(null)
            }}
            className="ml-1 rounded-lg border border-dashed px-2 py-1 text-[11px] font-medium"
            style={{ borderColor: withAlpha('#f4a945', 0.4), color: '#f4a945' }}
          >
            + มอบงานใหม่
          </button>
        </span>
      }
    >
      <div className="mb-2 flex items-center gap-1.5 text-[11px] text-[var(--color-cream-faint)]">
        เลื่อนซ้าย–ขวาเพื่อดูทุกคอลัมน์
        <span aria-hidden="true" className="text-[var(--color-cream-dim)]">→</span>
      </div>
      <div className="relative">
        <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-1" style={{ maxHeight: '82vh' }}>
        {BOARD_COLUMNS.map((status) => {
          const items = byStatus(status)
          const col = STATUS_HEX[status]
          return (
            <div key={status} className="flex w-[288px] shrink-0 flex-col">
              <div className="mb-2 flex items-center gap-2 px-1">
                <span className="h-2 w-2 rounded-full" style={{ background: col }} />
                <span className="text-[12px] font-medium text-[var(--color-cream)]">
                  {STATUS_LABEL[status]}
                </span>
                <span
                  className="ml-auto rounded-full px-1.5 text-[10px] tabular-nums"
                  style={{ background: withAlpha(col, 0.14), color: col }}
                >
                  {items.length}
                </span>
              </div>
              <div
                className="min-h-0 flex-1 space-y-2 overflow-x-hidden overflow-y-auto rounded-xl p-1.5"
                style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
              >
                {items.length === 0 && (
                  <div className="py-6 text-center text-[11px] text-[var(--color-cream-faint)]">
                    —
                  </div>
                )}
                {items.map((t: Task) => (
                  // Clicking a card opens the task control modal on top; keep the board open behind it.
                  <TaskCard key={t.id} task={t} compact />
                ))}
              </div>
            </div>
          )
        })}
        </div>
        <div
          className="pointer-events-none absolute inset-y-0 right-0 w-12"
          style={{ background: 'linear-gradient(to left, var(--color-surface-2), transparent)' }}
        />
      </div>
    </Modal>
  )
}
