import { useState } from 'react'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Drawer, Pill, Field, inputClass, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { relTime } from '../lib/format'
import type { DecisionStatus } from '../contract/types'

const STATUS_LABEL: Record<DecisionStatus, string> = {
  proposed: 'เสนอแล้ว',
  approved: 'อนุมัติแล้ว',
  rejected: 'ปฏิเสธ',
  superseded: 'ถูกแทนที่',
}

const STATUS_HEX: Record<DecisionStatus, string> = {
  proposed: ACCENT_HEX.amber,
  approved: ACCENT_HEX.teal,
  rejected: ACCENT_HEX.coral,
  superseded: ACCENT_HEX.lavender,
}

const SPECIAL_ACTOR: Record<string, string> = {
  system: 'ระบบ',
  executive: 'ผู้บริหาร',
  exec: 'ผู้บริหาร',
  user: 'คุณ',
}

export function DecisionsDrawer() {
  const open = useUI((s) => s.decisionsOpen)
  const toggle = useUI((s) => s.toggleDecisions)
  const [creating, setCreating] = useState(false)
  // subscribe to state bumps so the list re-reads client.getDecisions()
  const now = useSelector((s) => s.now)
  const deptNames = useSelector(
    (s) => Object.fromEntries(s.departments.map((d) => [d.id, `${d.emoji} ${d.name}`])),
    (a, b) => JSON.stringify(a) === JSON.stringify(b),
  )
  const decisions = [...client.getDecisions()].sort((a, b) => b.ts - a.ts)

  const actor = (id: string | null | undefined): string =>
    !id ? '—' : SPECIAL_ACTOR[id] ?? deptNames[id] ?? id

  return (
    <Drawer
      open={open}
      onClose={() => toggle(false)}
      title={
        <span className="flex items-center gap-2">
          บันทึกการตัดสินใจ
          {decisions.length > 0 && (
            <span className="text-[11px] font-normal text-[var(--color-cream-faint)]">
              {decisions.length} รายการ
            </span>
          )}
        </span>
      }
    >
      <div className="mb-2.5 flex justify-end">
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="rounded-xl px-3 py-1.5 text-xs font-semibold transition-opacity"
          style={{ background: ACCENT_HEX.amber, color: '#1a1610' }}
        >
          {creating ? 'ปิดฟอร์ม' : '+ บันทึกการตัดสินใจ'}
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-2.5 overflow-x-hidden overflow-y-auto pr-1">
        {creating && <CreateDecisionForm onDone={() => setCreating(false)} />}
        {decisions.length === 0 && !creating && (
          <div className="mt-12 text-center text-sm text-[var(--color-cream-faint)]">
            ยังไม่มีการตัดสินใจที่บันทึกไว้
          </div>
        )}
        {decisions.map((d) => {
          const hex = STATUS_HEX[d.status] ?? ACCENT_HEX.amber
          return (
            <div
              key={d.id}
              className="rounded-2xl border p-3"
              style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{d.title}</div>
                <Pill color={hex}>{STATUS_LABEL[d.status] ?? d.status}</Pill>
              </div>

              {d.rationale && (
                <div className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                  {d.rationale}
                </div>
              )}

              {d.impact && (
                <div className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]">
                  <span style={{ color: withAlpha(hex, 0.9) }}>ผลกระทบ: </span>
                  {d.impact}
                </div>
              )}

              {d.alternatives.length > 0 && (
                <div className="mt-1.5 text-[10px] text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]">
                  ทางเลือกที่พิจารณา: {d.alternatives.join(' · ')}
                </div>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
                <span>เสนอโดย {actor(d.proposedBy)}</span>
                {d.approvedBy && (
                  <>
                    <span>·</span>
                    <span>อนุมัติโดย {actor(d.approvedBy)}</span>
                  </>
                )}
                <span className="ml-auto">{relTime(d.ts, now)}ที่แล้ว</span>
              </div>

              {/* inline status edit */}
              <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t pt-2" style={{ borderColor: 'var(--color-line-soft)' }}>
                {(Object.keys(STATUS_LABEL) as DecisionStatus[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    disabled={s === d.status}
                    onClick={() =>
                      void client
                        .updateDecision(d.id, { status: s, approvedBy: s === 'approved' ? 'user' : undefined })
                        .catch(() => undefined)
                    }
                    className="rounded-lg border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40"
                    style={{ borderColor: withAlpha(STATUS_HEX[s], 0.4), color: STATUS_HEX[s] }}
                  >
                    {STATUS_LABEL[s]}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </Drawer>
  )
}

function CreateDecisionForm({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [rationale, setRationale] = useState('')
  const [impact, setImpact] = useState('')
  const [alternatives, setAlternatives] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = () => {
    if (!title.trim() || busy) return
    setBusy(true)
    void client
      .createDecision({
        title: title.trim(),
        proposedBy: 'user',
        rationale: rationale.trim() || null,
        impact: impact.trim(),
        alternatives: alternatives.split('\n').map((l) => l.trim()).filter(Boolean),
        status: 'proposed',
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }

  return (
    <div className="mb-1 rounded-2xl border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-2.5">
        <Field label="หัวข้อการตัดสินใจ">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="เช่น เลือกใช้ Provider Claude Code" />
        </Field>
        <Field label="เหตุผล (ไม่บังคับ)">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 52, resize: 'none' }} value={rationale} onChange={(e) => setRationale(e.target.value)} />
        </Field>
        <Field label="ผลกระทบ (ไม่บังคับ)">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={impact} onChange={(e) => setImpact(e.target.value)} />
        </Field>
        <Field label="ทางเลือกที่พิจารณา (บรรทัดละข้อ, ไม่บังคับ)">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 44, resize: 'none' }} value={alternatives} onChange={(e) => setAlternatives(e.target.value)} />
        </Field>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={onDone} className="rounded-xl border px-3 py-1.5 text-xs text-[var(--color-cream-dim)]" style={{ borderColor: 'var(--color-line-soft)' }}>ยกเลิก</button>
        <button type="button" onClick={submit} disabled={!title.trim() || busy} className="rounded-xl px-3.5 py-1.5 text-xs font-semibold disabled:opacity-40" style={{ background: ACCENT_HEX.amber, color: '#1a1610' }}>บันทึก</button>
      </div>
    </div>
  )
}
