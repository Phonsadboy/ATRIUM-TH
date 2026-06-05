import { useState } from 'react'
import type { ReactNode } from 'react'
import { useSelector, client, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Modal, Pill, Progress, withAlpha } from './primitives'
import { ACCENT_HEX, PRIORITY_HEX, PRIORITY_LABEL } from '../lib/visuals'
import { STATUS_HEX, originLabel, taskStatusLabel, isUserPaused, PAUSED_HEX } from '../lib/tasks'
import { relTime } from '../lib/format'
import type { Task, TaskControlAction } from '../contract/types'

type Tone = 'danger' | 'warn' | 'good' | 'neutral'

const TONE_HEX: Record<Tone, string> = {
  danger: ACCENT_HEX.coral,
  warn: ACCENT_HEX.honey,
  good: ACCENT_HEX.teal,
  neutral: ACCENT_HEX.sky,
}

interface ActionDef {
  label: string
  /** Confirmation warning copy shown before the action runs. */
  warn: string
  busyLabel: string
  tone: Tone
  /** Terminal actions close the modal on success. */
  terminal?: boolean
}

const ACTION_DEFS: Record<TaskControlAction, ActionDef> = {
  cancel: {
    label: 'ยกเลิกงาน',
    warn: 'ยกเลิกแล้วงานจะไม่กลับมาทำต่อ แต่ข้อมูลและชิ้นงานที่สร้างไว้จะยังอยู่',
    busyLabel: 'กำลังยกเลิก…',
    tone: 'danger',
    terminal: true,
  },
  pause: {
    label: 'หยุดงาน',
    warn: 'หยุดงานจะพักการทำงานของแผนกไว้ก่อน สามารถกดทำต่อได้ภายหลัง',
    busyLabel: 'กำลังหยุดงาน…',
    tone: 'warn',
  },
  resume: {
    label: 'ทำต่อ',
    warn: 'ให้แผนกกลับมาทำงานนี้ต่อจากที่หยุดไว้',
    busyLabel: 'กำลังสั่งทำต่อ…',
    tone: 'good',
  },
  submit_partial: {
    label: 'ส่งเท่าที่มี',
    warn: 'ระบบจะส่งผลลัพธ์ปัจจุบันให้ผู้บริหาร AI ตรวจ งานอาจถูกปิดหรือถูกส่งกลับให้แก้ต่อ',
    busyLabel: 'กำลังส่ง…',
    tone: 'neutral',
  },
  close: {
    label: 'ปิดงาน',
    warn: 'ปิดงานจะถือว่างานนี้จบแล้วทันที ไม่ส่งให้ผู้บริหาร AI ตรวจซ้ำ',
    busyLabel: 'กำลังปิดงาน…',
    tone: 'warn',
    terminal: true,
  },
}

/** Buttons available for the task's current state, in display order. */
function availableActions(task: Task): TaskControlAction[] {
  if (task.status === 'done' || task.status === 'cancelled') return []
  if (isUserPaused(task)) return ['resume', 'cancel', 'submit_partial', 'close']
  return ['cancel', 'pause', 'submit_partial', 'close']
}

function resultSummary(task: Task): string {
  const result = task.result as Record<string, unknown> | null | undefined
  const summary = result && typeof result.summary === 'string' ? result.summary.trim() : ''
  return summary
}

export function TaskControlModal() {
  const taskId = useUI((s) => s.taskControlTaskId)
  const close = useUI((s) => s.closeTaskControl)
  // Read the tasks array (depends only on company state) and resolve the task in the render
  // body. Resolving inside useSelector would cache against company-state identity and miss
  // taskId changes from the UI store — so closing (taskId → null) wouldn't update `task`.
  const tasks = useSelector((s) => s.tasks, shallowArrayEqual)
  const task = taskId ? tasks.find((t) => t.id === taskId) ?? null : null

  return (
    <Modal open={!!task} onClose={close} width={1180} title="จัดการงาน">
      {task && <TaskControlBody key={task.id} task={task} onClose={close} />}
    </Modal>
  )
}

function TaskControlBody({ task, onClose }: { task: Task; onClose: () => void }) {
  const departments = useSelector((s) => s.departments, shallowArrayEqual)
  const now = useSelector((s) => s.now)

  const [pending, setPending] = useState<TaskControlAction | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const getName = (id: string) => departments.find((d) => d.id === id)?.name ?? '—'
  const dept = departments.find((d) => d.id === task.departmentId)
  const paused = isUserPaused(task)
  const statusText = taskStatusLabel(task, getName)
  const statusColor = paused ? PAUSED_HEX : STATUS_HEX[task.status]
  const actions = availableActions(task)

  const beginAction = (action: TaskControlAction) => {
    setPending(action)
    setReason('')
    setError(null)
    setNotice(null)
  }

  const run = async () => {
    if (!pending || busy) return
    const def = ACTION_DEFS[pending]
    setBusy(true)
    setError(null)
    try {
      const res = await client.controlTask(task.id, {
        action: pending,
        reason: reason.trim() || undefined,
        requestedBy: 'user',
      })
      if (def.terminal && res.executed) {
        onClose()
        return
      }
      setPending(null)
      if (pending === 'submit_partial') setNotice('ส่งผลลัพธ์เท่าที่มีให้ผู้บริหาร AI ตรวจแล้ว')
      else if (pending === 'pause') setNotice('ผู้ใช้หยุดงานนี้ไว้ชั่วคราว')
      else if (pending === 'resume') setNotice('สั่งให้ทำงานต่อแล้ว')
      else if (!res.executed) setNotice('งานอยู่ในสถานะที่ทำรายการนี้ไม่ได้แล้ว')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const recentLogs = (task.log ?? []).slice(-20)
  const draft = (task.draftDeliverableMarkdown ?? '').trim()
  const summary = resultSummary(task)
  const content = (draft || summary).slice(0, 20000)

  return (
    <div className="space-y-4">
      {/* Header meta: status, owning department, who created it */}
      <div className="flex flex-wrap items-center gap-2">
        <Pill color={statusColor}>{statusText}</Pill>
        {dept && (
          <span className="text-[12px] text-[var(--color-cream-dim)]">
            {dept.emoji} {dept.name}
          </span>
        )}
        <span className="ml-auto text-[11px] text-[var(--color-cream-faint)]">
          สร้างโดย{originLabel(task.origin, getName)}
        </span>
      </div>

      {/* Section 1 — Summary */}
      <section className="space-y-3 rounded-xl border p-4" style={{ borderColor: 'var(--color-line-soft)' }}>
        <div className="text-[18px] font-semibold leading-snug text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
          {task.title}
        </div>
        {task.detail && (
          <div>
            <div className="mb-1 text-[10px] font-medium tracking-wide text-[var(--color-cream-faint)]">รายละเอียดงาน</div>
            <div
              className="max-h-56 overflow-y-auto rounded-lg border px-3 py-2.5 text-[13px] leading-relaxed whitespace-pre-wrap text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]"
              style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
            >
              {task.detail}
            </div>
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 pt-1 sm:grid-cols-3">
          <Row label="สถานะ">{statusText}</Row>
          <Row label="ความสำคัญ">
            <span style={{ color: PRIORITY_HEX[task.priority] }}>{PRIORITY_LABEL[task.priority]}</span>
          </Row>
          <Row label="แผนก">{dept ? `${dept.emoji} ${dept.name}` : 'ยังไม่มีแผนก'}</Row>
          <Row label="ความคืบหน้า">{Math.round((task.progress ?? 0) * 100)}%</Row>
          <Row label="สร้างเมื่อ">{relTime(task.createdAt, now)}</Row>
          <Row label="อัปเดตล่าสุด">{relTime(task.updatedAt, now)}</Row>
        </div>
      </section>

      {/* Section 2 — Current state */}
      <section className="space-y-2">
        <div className="text-[11px] font-medium tracking-wide text-[var(--color-cream-faint)]">สถานะปัจจุบัน</div>

        {paused && (
          <StateBox color={PAUSED_HEX}>ผู้ใช้หยุดงานนี้ไว้ชั่วคราว · กด “ทำต่อ” เพื่อให้แผนกทำงานต่อ</StateBox>
        )}

        {!paused && task.status === 'waiting' && (
          <StateBox color={STATUS_HEX.waiting}>
            {task.waitingOn?.dept && getName(task.waitingOn.dept) !== '—'
              ? `รอการตอบกลับจากฝ่าย${getName(task.waitingOn.dept)}`
              : 'รอผู้บริหาร AI ตรวจงาน'}
            {task.waitingOn?.reason ? ` · ${task.waitingOn.reason}` : ''}
            <MetaIds
              rows={[
                ['approval', task.waitingOn?.approvalId],
                ['decision', task.waitingOn?.decisionRequestId],
                ['handoff', task.waitingOn?.handoffId],
              ]}
            />
          </StateBox>
        )}

        {!paused && task.status === 'blocked' && (
          <StateBox color={STATUS_HEX.blocked}>
            ติดปัญหา: แผนกหรือเครื่องมือไปต่อไม่ได้
            {task.blockedLastReason ? ` · ${task.blockedLastReason}` : ''}
            <MetaIds
              rows={[
                ['retry', task.blockedRetryCount],
                ['guard', (task.blockedRetryGuard as Record<string, unknown> | null)?.status as string | undefined],
              ]}
            />
          </StateBox>
        )}

        {!paused && (task.status === 'in_progress' || task.status === 'review' || task.status === 'revising') && (
          <Progress value={task.progress} color={dept ? ACCENT_HEX[dept.accent] : ACCENT_HEX.teal} />
        )}
      </section>

      {/* Section 3 — Work content + activity log (two columns on wide screens) */}
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-1.5 lg:col-span-2">
          <div className="text-[11px] font-medium tracking-wide text-[var(--color-cream-faint)]">
            {draft ? 'เนื้อหางาน (ฉบับร่างล่าสุด)' : summary ? 'สรุปผลงานล่าสุด' : 'เนื้อหางาน'}
          </div>
          <div
            className="h-[32rem] overflow-y-auto rounded-lg border px-3.5 py-3 text-[13px] leading-relaxed whitespace-pre-wrap text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]"
            style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
          >
            {content ? content : <span className="text-[var(--color-cream-faint)]">ยังไม่มีเนื้อหา/ผลงานที่แผนกบันทึกไว้</span>}
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="text-[11px] font-medium tracking-wide text-[var(--color-cream-faint)]">บันทึกการทำงานล่าสุด</div>
          <ul
            className="h-[32rem] space-y-1.5 overflow-y-auto rounded-lg border px-3 py-2.5 text-[11px] leading-relaxed text-[var(--color-cream-faint)]"
            style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
          >
            {recentLogs.length > 0 ? (
              recentLogs.map((line, i) => (
                <li key={i} className="break-words [overflow-wrap:anywhere]">
                  · {line}
                </li>
              ))
            ) : (
              <li className="text-[var(--color-cream-faint)]">ยังไม่มีบันทึก</li>
            )}
          </ul>
        </div>
      </section>

      {/* Section 3 — Actions */}
      {pending ? (
        <ConfirmPanel
          def={ACTION_DEFS[pending]}
          reason={reason}
          onReason={setReason}
          busy={busy}
          onConfirm={run}
          onCancel={() => setPending(null)}
        />
      ) : actions.length > 0 ? (
        <div className="grid grid-cols-2 gap-2">
          {actions.map((action) => {
            const def = ACTION_DEFS[action]
            const hex = TONE_HEX[def.tone]
            return (
              <button
                key={action}
                type="button"
                onClick={() => beginAction(action)}
                className="rounded-xl border py-2.5 text-[13px] font-medium transition-colors"
                style={{ borderColor: withAlpha(hex, 0.4), color: hex, background: withAlpha(hex, 0.1) }}
              >
                {def.label}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="rounded-xl border px-3 py-2.5 text-[12px] text-[var(--color-cream-faint)]" style={{ borderColor: 'var(--color-line-soft)' }}>
          งานนี้จบแล้ว ไม่มีการดำเนินการเพิ่มเติม
        </div>
      )}

      {notice && (
        <div
          className="rounded-xl border px-3 py-2 text-[12px] leading-relaxed"
          style={{ borderColor: withAlpha(ACCENT_HEX.teal, 0.4), background: withAlpha(ACCENT_HEX.teal, 0.12), color: 'var(--color-cream)' }}
        >
          {notice}
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-xl border px-3 py-2 text-[12px] leading-relaxed"
          style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.42), background: withAlpha(ACCENT_HEX.coral, 0.12), color: 'var(--color-cream)' }}
        >
          <div className="font-semibold">ทำรายการไม่สำเร็จ</div>
          <div className="mt-1 text-[var(--color-cream-dim)]">{error}</div>
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-[12px]">
      <span className="w-20 shrink-0 text-[var(--color-cream-faint)]">{label}</span>
      <span className="min-w-0 flex-1 text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{children}</span>
    </div>
  )
}

function StateBox({ color, children }: { color: string; children: ReactNode }) {
  return (
    <div
      className="rounded-lg border px-2.5 py-2 text-[11px] leading-relaxed text-[var(--color-cream-dim)]"
      style={{ borderColor: `${color}55`, background: `${color}14` }}
    >
      {children}
    </div>
  )
}

function MetaIds({ rows }: { rows: Array<[string, unknown]> }) {
  const present = rows.filter(([, value]) => value !== undefined && value !== null && value !== '')
  if (present.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[var(--color-cream-faint)]">
      {present.map(([label, value]) => (
        <span key={label}>
          {label}: {String(value)}
        </span>
      ))}
    </div>
  )
}

function ConfirmPanel({
  def,
  reason,
  onReason,
  busy,
  onConfirm,
  onCancel,
}: {
  def: ActionDef
  reason: string
  onReason: (value: string) => void
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const hex = TONE_HEX[def.tone]
  return (
    <div className="space-y-3 rounded-xl border p-3" style={{ borderColor: withAlpha(hex, 0.4), background: withAlpha(hex, 0.08) }}>
      <div className="text-[12px] leading-relaxed text-[var(--color-cream)]">{def.warn}</div>
      <textarea
        value={reason}
        onChange={(e) => onReason(e.target.value)}
        placeholder="เหตุผล (ไม่บังคับ)"
        className="w-full rounded-lg border bg-transparent px-2.5 py-2 text-[12px] text-[var(--color-cream)] outline-none"
        style={{ borderColor: 'var(--color-line-soft)', minHeight: 52, resize: 'none' }}
      />
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded-xl border px-4 py-2 text-[13px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)] disabled:opacity-40"
          style={{ borderColor: 'var(--color-line-soft)' }}
        >
          ยกเลิก
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="rounded-xl px-4 py-2 text-[13px] font-semibold transition-opacity disabled:opacity-50"
          style={{ background: hex, color: '#1a1610' }}
        >
          {busy ? def.busyLabel : `ยืนยัน ${def.label}`}
        </button>
      </div>
    </div>
  )
}
