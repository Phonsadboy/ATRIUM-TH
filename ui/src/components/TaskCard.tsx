import { useRef } from 'react'
import { motion } from 'framer-motion'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Progress, Pill, cx } from './primitives'
import { ProblemDetails } from './ProblemDetails'
import { PRIORITY_HEX, PRIORITY_LABEL, ACCENT_HEX } from '../lib/visuals'
import { STATUS_HEX, originLabel, taskStatusLabel, isUserPaused, PAUSED_HEX } from '../lib/tasks'
import { relTime } from '../lib/format'
import type { Task } from '../contract/types'

function stringValue(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function lastLogLine(task: Task): string {
  const log = Array.isArray(task.log) ? task.log : []
  return stringValue(log[log.length - 1])
}

export function TaskCard({ task, compact }: { task: Task; compact?: boolean }) {
  const departments = useSelector((s) => s.departments)
  const now = useSelector((s) => s.now)
  const openTaskControl = useUI((s) => s.openTaskControl)

  const dept = departments.find((d) => d.id === task.departmentId)
  const getName = (id: string) => departments.find((d) => d.id === id)?.name ?? '—'
  const statusText = taskStatusLabel(task, getName)
  const waitingOnName = task.status === 'waiting' && task.waitingOn?.dept ? getName(task.waitingOn.dept) : ''
  const waitingHandoff = task.status === 'waiting'
    ? task.handoffs.find((handoff) => handoff.id === task.waitingOn?.handoffId)
    : undefined
  const waitingPacket = waitingHandoff?.contextPacketFilename || waitingHandoff?.contextPacketArtifactId || ''
  const waitingReason = task.waitingOn?.reason || ''
  const waitingReasonLabels: Record<string, string> = {
    handoff_reply: 'รอคำตอบจาก handoff',
    missing_file: 'รอไฟล์หรือหลักฐาน',
    clarification: 'รอคำถาม/คำชี้แจง',
    executive_decision: 'รอ AI ผู้บริหารตัดสิน',
    blocked_retry_guard: 'หยุดรันอัตโนมัติ',
  }
  const waitingReasonText = waitingReasonLabels[waitingReason] ?? waitingReason
  const waitingStatus = waitingHandoff?.status ? ` · status: ${waitingHandoff.status}` : ''
  const blockedGuard = task.blockedRetryGuard && typeof task.blockedRetryGuard === 'object'
    ? task.blockedRetryGuard as Record<string, unknown>
    : null
  const blockedFrozen = blockedGuard?.status === 'frozen'
  const blockedReason = stringValue(task.blockedLastReason) || stringValue(task.statusReason) || lastLogLine(task)
  const active =
    task.status === 'in_progress' ||
    task.status === 'review' ||
    task.status === 'revising'
  const cancelled = task.status === 'cancelled'
  const reviewMinutes = task.reviewIntervalMs ? Math.max(1, Math.round(task.reviewIntervalMs / 60_000)) : ''
  const reviewInputRef = useRef<HTMLInputElement>(null)

  const saveReviewSchedule = () => {
    const minutes = Number(reviewInputRef.current?.value ?? reviewMinutes)
    void client.updateTaskReviewSchedule(task.id, minutes > 0 ? Math.round(minutes) * 60_000 : null)
  }

  return (
    <motion.div
      role="button"
      tabIndex={0}
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: cancelled ? 0.55 : 1, y: 0 }}
      onClick={() => openTaskControl(task.id)}
      onKeyDown={(event) => {
        const target = event.target as HTMLElement | null
        if (target?.closest('button,input,textarea,select,a')) return
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          openTaskControl(task.id)
        }
      }}
      className="w-full cursor-pointer rounded-2xl border border-[var(--color-line-soft)] bg-[var(--color-surface-2)] p-3 text-left transition-colors hover:border-[var(--color-line)] hover:bg-[var(--color-surface-3)]"
      title="คลิกเพื่อดูและจัดการงาน"
      aria-label={`จัดการงาน ${task.title}`}
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
        <Pill color={isUserPaused(task) ? PAUSED_HEX : STATUS_HEX[task.status]}>{statusText}</Pill>
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

      {task.status === 'waiting' && (
        <div
          className="mt-2 rounded-md border px-2 py-1.5 text-[10px] leading-relaxed text-[var(--color-cream-dim)]"
          style={{
            borderColor: `${STATUS_HEX.waiting}55`,
            background: `${STATUS_HEX.waiting}14`,
          }}
        >
          {waitingOnName && waitingOnName !== '—'
            ? `รอการตอบกลับจากฝ่าย${waitingOnName}`
            : 'รอการตอบกลับจากแผนกที่เกี่ยวข้อง'}
          {waitingReasonText ? ` · ${waitingReasonText}` : ''}
          {waitingStatus}
          {waitingPacket ? ` · packet: ${waitingPacket}` : ''}
          <ProblemDetails
            className="mt-1.5"
            color={STATUS_HEX.waiting}
            summary="ดูว่ารออยู่จุดไหน"
            rows={[
              { label: 'จุดที่หยุด', value: waitingOnName && waitingOnName !== '—' ? `ฝ่าย${waitingOnName}` : 'handoff/approval' },
              { label: 'สาเหตุ', value: waitingReasonText || waitingReason },
              { label: 'handoff', value: waitingHandoff?.id },
              { label: 'สถานะ', value: waitingHandoff?.status },
              { label: 'เหตุผลสถานะ', value: waitingHandoff?.statusReason },
              { label: 'packet', value: waitingPacket },
              { label: 'approval', value: task.waitingOn?.approvalId },
              { label: 'decision', value: task.waitingOn?.decisionRequestId },
            ]}
          />
        </div>
      )}

      {task.status === 'blocked' && !isUserPaused(task) && (
        <div
          className="mt-2 rounded-md border px-2 py-1.5 text-[10px] leading-relaxed text-[var(--color-cream-dim)]"
          style={{
            borderColor: `${STATUS_HEX.blocked}55`,
            background: `${STATUS_HEX.blocked}14`,
          }}
        >
          {blockedFrozen
            ? 'หยุดรันอัตโนมัติ: ให้ AI ผู้บริหารตัดสินแล้ว'
            : 'ติดปัญหา: แผนกหรือเครื่องมือไปต่อไม่ได้ รอผู้บริหารตรวจสาเหตุจากแชทและบันทึกงาน'}
          {blockedGuard?.executiveAction ? ` · action: ${String(blockedGuard.executiveAction)}` : ''}
          <ProblemDetails
            className="mt-1.5"
            color={STATUS_HEX.blocked}
            summary="ดูว่าติดที่จุดไหนและเพราะอะไร"
            rows={[
              { label: 'จุดที่หยุด', value: dept?.name ? `ฝ่าย${dept.name}` : task.departmentId },
              { label: 'สาเหตุ', value: blockedReason },
              { label: 'guard', value: blockedGuard?.status },
              { label: 'จำนวน retry', value: task.blockedRetryCount },
              { label: 'action', value: blockedGuard?.executiveAction },
              { label: 'reason', value: blockedGuard?.reason },
              { label: 'ล่าสุด', value: task.lastUnblockAttemptAt ? relTime(task.lastUnblockAttemptAt, now) : '' },
            ]}
          />
        </div>
      )}

      {isUserPaused(task) && (
        <div
          className="mt-2 rounded-md border px-2 py-1.5 text-[10px] leading-relaxed text-[var(--color-cream-dim)]"
          style={{ borderColor: `${PAUSED_HEX}55`, background: `${PAUSED_HEX}14` }}
        >
          ผู้ใช้หยุดงานนี้ไว้ชั่วคราว · กดที่การ์ดเพื่อสั่งทำต่อหรือจัดการงาน
        </div>
      )}

      {!compact && !cancelled && (
        <div
          className="mt-2 flex items-center gap-1.5 text-[10px] text-[var(--color-cream-faint)]"
          onClick={(event) => event.stopPropagation()}
        >
          <span>ปลุกตรวจทุก</span>
          <input
            key={`${task.id}:${reviewMinutes}`}
            ref={reviewInputRef}
            type="number"
            min={0}
            step={1}
            defaultValue={String(reviewMinutes)}
            className="h-7 w-16 rounded-md border bg-transparent px-2 text-[11px] text-[var(--color-cream)] outline-none"
            style={{ borderColor: 'var(--color-line-soft)' }}
          />
          <span>นาที</span>
          <button
            type="button"
            onClick={saveReviewSchedule}
            className="ml-auto rounded-md border px-2 py-1 text-[10px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
            style={{ borderColor: 'var(--color-line-soft)' }}
          >
            บันทึก
          </button>
        </div>
      )}
    </motion.div>
  )
}
