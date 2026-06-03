import { motion } from 'framer-motion'
import { useSelector } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Progress, Pill, cx } from './primitives'
import { PRIORITY_HEX, PRIORITY_LABEL, ACCENT_HEX } from '../lib/visuals'
import { STATUS_HEX, STATUS_LABEL, originLabel } from '../lib/tasks'
import { relTime } from '../lib/format'
import type { Task } from '../contract/types'

export function TaskCard({ task, compact }: { task: Task; compact?: boolean }) {
  const departments = useSelector((s) => s.departments)
  const now = useSelector((s) => s.now)
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)

  const dept = departments.find((d) => d.id === task.departmentId)
  const getName = (id: string) => departments.find((d) => d.id === id)?.name ?? '—'
  const active =
    task.status === 'in_progress' ||
    task.status === 'review' ||
    task.status === 'revising'
  const cancelled = task.status === 'cancelled'

  return (
    <motion.button
      type="button"
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: cancelled ? 0.55 : 1, y: 0 }}
      onClick={() => {
        if (task.departmentId) {
          select(task.departmentId)
          setRightTab('tasks')
        }
      }}
      className="w-full rounded-2xl border p-3 text-left transition-colors hover:border-[var(--color-line)]"
      style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
    >
      <div className="flex items-start gap-2">
        <span
          className="mt-1 h-2 w-2 shrink-0 rounded-full"
          style={{ background: PRIORITY_HEX[task.priority], boxShadow: `0 0 6px ${PRIORITY_HEX[task.priority]}` }}
          title={`ความสำคัญ: ${PRIORITY_LABEL[task.priority]}`}
        />
        <div className="min-w-0 flex-1">
          <div
            className={cx(
              'text-[13px] leading-snug font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]',
              cancelled && 'line-through',
            )}
          >
            {task.title}
          </div>
          {!compact && task.detail && (
            <div className="mt-0.5 line-clamp-2 text-[11px] text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]">
              {task.detail}
            </div>
          )}
        </div>
      </div>

      {active && (
        <div className="mt-2">
          <Progress value={task.progress} color={dept ? ACCENT_HEX[dept.accent] : ACCENT_HEX.teal} />
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Pill color={STATUS_HEX[task.status]}>{STATUS_LABEL[task.status]}</Pill>
        {dept && (
          <span className="text-[11px] text-[var(--color-cream-dim)]">
            {dept.emoji} {dept.name}
          </span>
        )}
        {task.handoffs.length > 0 && (
          <span
            className="text-[10px]"
            style={{ color: ACCENT_HEX.lavender }}
            title={`ส่งต่อ ${task.handoffs.length} ครั้ง`}
          >
            ⇄ {task.handoffs.length}
          </span>
        )}
        <span className="ml-auto text-[10px] text-[var(--color-cream-faint)]">
          {relTime(task.updatedAt, now)}
        </span>
      </div>

      <div className="mt-1 text-[10px] text-[var(--color-cream-faint)]">
        จาก {originLabel(task.origin, getName)}
      </div>
    </motion.button>
  )
}
