import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, Pill, inputClass } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { Playbook } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync } from './shared'

const lines = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean)
const csv = (s: string) => s.split(',').map((l) => l.trim()).filter(Boolean)

export function PlaybooksPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listPlaybooks({ limit: 100 }), [])
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const playbooks = data ?? []

  return (
    <Section title="คู่มือปฏิบัติ (Playbooks)" hint="ขั้นตอนงานมาตรฐานที่นำกลับมาใช้ซ้ำได้" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ คู่มือ'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : playbooks.length === 0 && !error ? <Empty text="ยังไม่มีคู่มือ" /> : (
        <div className="space-y-2.5">
          {playbooks.map((p) => {
            const open = openId === p.id
            return (
              <Row key={p.id} accent={ACCENT_HEX.lavender}>
                <button type="button" onClick={() => setOpenId(open ? null : p.id)} className="block w-full text-left">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{p.name}</div>
                    <Pill color={ACCENT_HEX.lavender}>v{p.version}</Pill>
                  </div>
                  <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{p.whenToUse}</div>
                </button>
                {open && <Detail p={p} />}
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}

function Detail({ p }: { p: Playbook }) {
  return (
    <div className="mt-3 space-y-2 border-t pt-3 text-[11px] text-[var(--color-cream-dim)]" style={{ borderColor: 'var(--color-line-soft)' }}>
      {p.steps.length > 0 && (
        <div><span className="text-[var(--color-cream-faint)]">ขั้นตอน:</span><ol className="mt-0.5 list-decimal pl-4">{p.steps.map((s, i) => <li key={i}>{s}</li>)}</ol></div>
      )}
      {p.requiredSkills.length > 0 && <div><span className="text-[var(--color-cream-faint)]">ทักษะที่ต้องใช้:</span> {p.requiredSkills.join(', ')}</div>}
      {p.deliverableSpec && <div><span className="text-[var(--color-cream-faint)]">สเปกผลงาน:</span> {p.deliverableSpec}</div>}
      {p.qualityChecklist.length > 0 && <div><span className="text-[var(--color-cream-faint)]">เช็กลิสต์คุณภาพ:</span><ul className="mt-0.5 list-disc pl-4">{p.qualityChecklist.map((s, i) => <li key={i}>{s}</li>)}</ul></div>}
    </div>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [whenToUse, setWhenToUse] = useState('')
  const [steps, setSteps] = useState('')
  const [skills, setSkills] = useState('')
  const [spec, setSpec] = useState('')
  const [checklist, setChecklist] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (!name.trim() || !whenToUse.trim() || busy) return
    setBusy(true)
    void client.createPlaybook({
      name: name.trim(),
      whenToUse: whenToUse.trim(),
      steps: lines(steps),
      requiredSkills: csv(skills),
      deliverableSpec: spec.trim(),
      qualityChecklist: lines(checklist),
    }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="ชื่อคู่มือ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="เช่น ออกบทความ SEO" /></Field>
        <Field label="ใช้เมื่อไร"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} placeholder="เช่น เมื่อมีงานเขียนบทความ" /></Field>
        <Field label="ขั้นตอน (บรรทัดละข้อ)"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 56, resize: 'none' }} value={steps} onChange={(e) => setSteps(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="ทักษะ (คั่นจุลภาค)"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={skills} onChange={(e) => setSkills(e.target.value)} /></Field>
          <Field label="สเปกผลงาน"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={spec} onChange={(e) => setSpec(e.target.value)} /></Field>
        </div>
        <Field label="เช็กลิสต์คุณภาพ (บรรทัดละข้อ)"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 48, resize: 'none' }} value={checklist} onChange={(e) => setChecklist(e.target.value)} /></Field>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!name.trim() || !whenToUse.trim() || busy}>สร้างคู่มือ</PrimaryBtn></div>
    </div>
  )
}
