import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { useSelector } from '../../state/useCompany'
import { isExec } from '../../lib/threads'
import type { Project, ProjectStatus, ProjectReviewStatus } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts } from './shared'

const STATUS: Record<ProjectStatus, { label: string; hex: string }> = {
  active: { label: 'กำลังทำ', hex: ACCENT_HEX.teal },
  paused: { label: 'พัก', hex: ACCENT_HEX.honey },
  done: { label: 'เสร็จ', hex: ACCENT_HEX.sky },
  archived: { label: 'เก็บเข้าคลัง', hex: ACCENT_HEX.lavender },
}

const REVIEW: Record<ProjectReviewStatus, { label: string; hex: string }> = {
  draft: { label: 'ร่าง', hex: ACCENT_HEX.lavender },
  pending_user: { label: 'รอคุณอนุมัติ', hex: ACCENT_HEX.coral },
  approved_by_user: { label: 'คุณอนุมัติแล้ว', hex: ACCENT_HEX.teal },
  rejected_by_user: { label: 'คุณตีกลับ', hex: ACCENT_HEX.coral },
}

export function ProjectsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listProjects({ limit: 200 }), [])
  const { label } = useDepts()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)

  const projects = data ?? []

  return (
    <Section
      title="โปรเจกต์"
      hint="งานหลายแผนกที่มีการตรวจสองชั้น (แผนก → คุณ)"
      actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ โปรเจกต์ใหม่'}</PrimaryBtn>}
    >
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : projects.length === 0 && !error ? (
        <Empty text="ยังไม่มีโปรเจกต์ · กด “+ โปรเจกต์ใหม่” เพื่อเริ่ม" />
      ) : (
        <div className="space-y-2.5">
          {projects.map((p) => (
            <ProjectRow
              key={p.id}
              p={p}
              deptLabel={label}
              open={openId === p.id}
              onToggle={() => setOpenId(openId === p.id ? null : p.id)}
              onChanged={reload}
            />
          ))}
        </div>
      )}
    </Section>
  )
}

function ProjectRow({
  p,
  deptLabel,
  open,
  onToggle,
  onChanged,
}: {
  p: Project
  deptLabel: (id: string | null | undefined) => string
  open: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const now = useSelector((s) => s.now)
  const st = STATUS[p.status]
  const rev = p.reviewStatus ? REVIEW[p.reviewStatus] : null

  const setStatus = (status: ProjectStatus) => {
    void client.updateProject(p.id, { status }).then(onChanged).catch(() => undefined)
  }
  const resolve = (decision: 'approved' | 'rejected') => {
    void client
      .resolveProject(p.id, { decision, deliverableArtifactId: p.deliverableArtifactId ?? null })
      .then(onChanged)
      .catch(() => undefined)
  }

  return (
    <Row accent={st.hex}>
      <button type="button" onClick={onToggle} className="block w-full text-left">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{p.name}</div>
          <div className="flex shrink-0 items-center gap-1.5">
            {rev && <Pill color={rev.hex}>{rev.label}</Pill>}
            <Pill color={st.hex}>{st.label}</Pill>
          </div>
        </div>
        <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{p.goal}</div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
          {p.departments.length > 0 && <span>{p.departments.map(deptLabel).join(' · ')}</span>}
          {p.lead && <span>· นำโดย {deptLabel(p.lead)}</span>}
          <span className="ml-auto">{relTime(p.createdAt, now)}ที่แล้ว</span>
        </div>
      </button>

      {open && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: 'var(--color-line-soft)' }}>
          {p.deliverableArtifactId && (
            <div className="mb-2 text-[11px] text-[var(--color-cream-faint)]">
              ผลงานส่งมอบ: <span className="text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{p.deliverableArtifactId}</span>
            </div>
          )}
          {p.reviewStatus === 'pending_user' && (
            <div className="mb-2 flex gap-2">
              <button
                type="button"
                onClick={() => resolve('approved')}
                className="flex-1 rounded-xl py-2 text-xs font-semibold"
                style={{ background: withAlpha(ACCENT_HEX.teal, 0.18), color: ACCENT_HEX.teal, border: `1px solid ${withAlpha(ACCENT_HEX.teal, 0.4)}` }}
              >
                อนุมัติส่งมอบ
              </button>
              <button
                type="button"
                onClick={() => resolve('rejected')}
                className="flex-1 rounded-xl py-2 text-xs font-semibold"
                style={{ background: withAlpha(ACCENT_HEX.coral, 0.14), color: ACCENT_HEX.coral, border: `1px solid ${withAlpha(ACCENT_HEX.coral, 0.35)}` }}
              >
                ตีกลับ
              </button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[10px] text-[var(--color-cream-faint)]">เปลี่ยนสถานะ:</span>
            {(Object.keys(STATUS) as ProjectStatus[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatus(s)}
                disabled={s === p.status}
                className="rounded-lg border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40"
                style={{ borderColor: withAlpha(STATUS[s].hex, 0.4), color: STATUS[s].hex }}
              >
                {STATUS[s].label}
              </button>
            ))}
          </div>
        </div>
      )}
    </Row>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.filter((d) => !isExec(d.id)).map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [name, setName] = useState('')
  const [goal, setGoal] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [lead, setLead] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))

  const submit = () => {
    if (!name.trim() || !goal.trim() || busy) return
    setBusy(true)
    void client
      .createProject({
        name: name.trim(),
        goal: goal.trim(),
        departments: selected,
        lead: lead || null,
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }

  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="ชื่อโปรเจกต์">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="เช่น เปิดตัวสินค้า Q3" />
        </Field>
        <Field label="เป้าหมาย">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 56, resize: 'none' }} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="อธิบายผลลัพธ์ที่ต้องการ" />
        </Field>
        <Field label="แผนกที่เกี่ยวข้อง">
          <div className="flex flex-wrap gap-1.5">
            {depts.length === 0 && <span className="text-[11px] text-[var(--color-cream-faint)]">ยังไม่มีแผนก</span>}
            {depts.map((d) => {
              const on = selected.includes(d.id)
              return (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => toggle(d.id)}
                  className="rounded-lg border px-2 py-1 text-[11px] transition-colors"
                  style={{
                    borderColor: on ? withAlpha(ACCENT_HEX.teal, 0.5) : 'var(--color-line-soft)',
                    background: on ? withAlpha(ACCENT_HEX.teal, 0.12) : 'transparent',
                    color: on ? ACCENT_HEX.teal : 'var(--color-cream-dim)',
                  }}
                >
                  {d.emoji} {d.name}
                </button>
              )
            })}
          </div>
        </Field>
        {selected.length > 0 && (
          <Field label="แผนกนำ (ไม่บังคับ)">
            <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={lead} onChange={(e) => setLead(e.target.value)}>
              <option value="">— ไม่ระบุ —</option>
              {selected.map((id) => {
                const d = depts.find((x) => x.id === id)
                return <option key={id} value={id}>{d ? `${d.emoji} ${d.name}` : id}</option>
              })}
            </select>
          </Field>
        )}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <GhostBtn onClick={onDone}>ยกเลิก</GhostBtn>
        <PrimaryBtn onClick={submit} disabled={!name.trim() || !goal.trim() || busy}>สร้างโปรเจกต์</PrimaryBtn>
      </div>
    </div>
  )
}
