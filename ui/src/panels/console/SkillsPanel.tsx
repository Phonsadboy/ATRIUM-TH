import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, Pill, inputClass } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { Skill } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync } from './shared'

const csv = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)

export function SkillsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listSkills({ limit: 200 }), [])
  const [creating, setCreating] = useState(false)
  const skills = data ?? []

  return (
    <Section title="ทักษะ (Skills)" hint="คลังทักษะที่แผนกหยิบไปใช้ได้" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ ทักษะ'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : skills.length === 0 && !error ? <Empty text="ยังไม่มีทักษะในคลัง" /> : (
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {skills.map((s: Skill) => (
            <Row key={s.id} accent={ACCENT_HEX.honey}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{s.name}</div>
                {s.suggestedModel && <Pill color={ACCENT_HEX.honey}>{s.suggestedModel}</Pill>}
              </div>
              <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{s.description}</div>
              {s.tools.length > 0 && <div className="mt-1 text-[10px] text-[var(--color-cream-faint)]">เครื่องมือ: {s.tools.join(', ')}</div>}
            </Row>
          ))}
        </div>
      )}
    </Section>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [tools, setTools] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (!name.trim() || !description.trim() || busy) return
    setBusy(true)
    void client.createSkill({ name: name.trim(), description: description.trim(), tools: csv(tools), suggestedModel: model.trim() || null }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="ชื่อทักษะ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="เช่น เขียน Copy โฆษณา" /></Field>
        <Field label="คำอธิบาย"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 48, resize: 'none' }} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="เครื่องมือ (คั่นจุลภาค)"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={tools} onChange={(e) => setTools(e.target.value)} /></Field>
          <Field label="โมเดลแนะนำ (ไม่บังคับ)"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={model} onChange={(e) => setModel(e.target.value)} placeholder="เช่น claude-sonnet-4-6" /></Field>
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!name.trim() || !description.trim() || busy}>เพิ่มทักษะ</PrimaryBtn></div>
    </div>
  )
}
