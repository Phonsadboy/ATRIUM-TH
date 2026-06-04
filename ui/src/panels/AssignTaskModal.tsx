import { useState } from 'react'
import { useSelector, client, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Field, Modal, Toggle, inputClass, withAlpha } from '../components/primitives'
import { ACCENT_HEX, PRIORITY_HEX, PRIORITY_LABEL } from '../lib/visuals'
import { EXEC_ID } from '../lib/threads'
import type { Priority } from '../contract/types'

const PRIORITIES: Priority[] = ['low', 'normal', 'high', 'urgent']

type DeptOption = { id: string; name: string; emoji: string }

export function AssignTaskModal() {
  const open = useUI((s) => s.assignOpen)
  const assignDeptId = useUI((s) => s.assignDeptId)
  const close = useUI((s) => s.closeAssign)

  const departments = useSelector(
    (s) =>
      s.departments
        .filter((d) => d.id !== EXEC_ID)
        .map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })),
    shallowArrayEqual,
  )

  const presetDeptId =
    assignDeptId && assignDeptId !== EXEC_ID && departments.some((d) => d.id === assignDeptId)
      ? assignDeptId
      : departments[0]?.id ?? ''
  const presetByExec = assignDeptId === EXEC_ID || assignDeptId == null

  return (
    <Modal open={open} onClose={close} width={480} title="มอบงานใหม่">
      {/* Keying by (open + assignDeptId) remounts the form so its useState
          initializers preset the department and reset the fields on open —
          no set-state-in-effect needed. */}
      <AssignTaskForm
        key={`${open ? 'open' : 'closed'}:${assignDeptId ?? ''}`}
        departments={departments}
        initialDeptId={presetDeptId}
        initialByExec={presetByExec}
        onClose={close}
      />
    </Modal>
  )
}

function AssignTaskForm({
  departments,
  initialDeptId,
  initialByExec,
  onClose,
}: {
  departments: DeptOption[]
  initialDeptId: string
  initialByExec: boolean
  onClose: () => void
}) {
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)

  const [title, setTitle] = useState('')
  const [detail, setDetail] = useState('')
  const [deptId, setDeptId] = useState(initialDeptId)
  const [priority, setPriority] = useState<Priority>('normal')
  const [byExec, setByExec] = useState(initialByExec)
  const [reviewMinutes, setReviewMinutes] = useState('5')

  const canSubmit = title.trim() !== '' && deptId !== ''

  const submit = () => {
    if (!canSubmit) return
    const reviewIntervalMs = Math.max(1, Number(reviewMinutes) || 5) * 60_000
    client.assignTask({
      title: title.trim(),
      detail: detail.trim() || undefined,
      departmentId: deptId,
      priority,
      byExecutive: byExec,
      reviewIntervalMs,
    })
    select(deptId)
    setRightTab('tasks')
    onClose()
  }

  return (
    <>
      <div className="space-y-3">
        <Field label="ชื่องาน">
          <input
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="เช่น ทำสไลด์นำเสนอนักลงทุน"
            autoFocus
          />
        </Field>

        <Field label="รายละเอียด (ไม่บังคับ)">
          <textarea
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)', minHeight: 64, resize: 'none' }}
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder="ขอบเขต ข้อกำหนด หรือสิ่งที่ต้องการ"
          />
        </Field>

        <Field label="มอบให้ฝ่าย">
          <select
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={deptId}
            onChange={(e) => setDeptId(e.target.value)}
          >
            {departments.length === 0 && <option value="">— ยังไม่มีแผนก —</option>}
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.emoji} {d.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="ความสำคัญ">
          <div className="flex gap-2">
            {PRIORITIES.map((p) => {
              const on = priority === p
              const col = PRIORITY_HEX[p]
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPriority(p)}
                  className="flex-1 rounded-xl border py-2 text-[12px] font-medium transition-colors"
                  style={{
                    borderColor: on ? withAlpha(col, 0.5) : 'var(--color-line-soft)',
                    background: on ? withAlpha(col, 0.14) : 'transparent',
                    color: on ? col : 'var(--color-cream-dim)',
                  }}
                >
                  {PRIORITY_LABEL[p]}
                </button>
              )
            })}
          </div>
        </Field>

        <Field label="ปลุกตรวจงานทุก (นาที)">
          <input
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            type="number"
            min={1}
            step={1}
            value={reviewMinutes}
            onChange={(e) => setReviewMinutes(e.target.value)}
          />
        </Field>

        <div
          className="flex items-center justify-between rounded-xl border px-3 py-2.5"
          style={{ borderColor: 'var(--color-line-soft)' }}
        >
          <div>
            <div className="text-[12px] font-medium text-[var(--color-cream)]">
              มอบหมายในนามผู้บริหาร
            </div>
            <div className="text-[10px] text-[var(--color-cream-faint)]">
              บันทึกที่มาเป็นผู้บริหารแทนการสั่งตรง
            </div>
          </div>
          <Toggle on={byExec} onChange={setByExec} accent={ACCENT_HEX.amber} />
        </div>
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-xl border px-4 py-2 text-sm text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
          style={{ borderColor: 'var(--color-line-soft)' }}
        >
          ยกเลิก
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-xl px-4 py-2 text-sm font-semibold transition-opacity disabled:opacity-40"
          style={{ background: ACCENT_HEX.amber, color: '#1a1610' }}
        >
          มอบงาน
        </button>
      </div>
    </>
  )
}
