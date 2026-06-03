import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { Preference, PreferenceCategory } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, useAsync } from './shared'

const CATEGORY: Record<PreferenceCategory, { label: string; hex: string }> = {
  comm: { label: 'การสื่อสาร', hex: ACCENT_HEX.sky },
  code: { label: 'การเขียนโค้ด', hex: ACCENT_HEX.teal },
  design: { label: 'ดีไซน์', hex: ACCENT_HEX.lavender },
  risk: { label: 'ความเสี่ยง', hex: ACCENT_HEX.coral },
  budget: { label: 'งบประมาณ', hex: ACCENT_HEX.amber },
  general: { label: 'ทั่วไป', hex: ACCENT_HEX.honey },
}
const CATS = Object.keys(CATEGORY) as PreferenceCategory[]

export function OwnerPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listPreferences({ limit: 200 }), [])
  const [adding, setAdding] = useState(false)
  const prefs = data ?? []

  return (
    <Section title="โปรไฟล์เจ้าของ · ความชอบ" hint="กฎและความชอบที่ทุกแผนกยึดเป็นแนวทาง" actions={<PrimaryBtn onClick={() => setAdding((v) => !v)}>{adding ? 'ปิดฟอร์ม' : '+ ความชอบ'}</PrimaryBtn>}>
      {adding && <CreateForm onDone={() => { setAdding(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : prefs.length === 0 && !error ? <Empty text="ยังไม่มีความชอบที่บันทึกไว้" /> : (
        <div className="space-y-2">
          {prefs.map((p) => <PrefRow key={p.id} p={p} onChanged={reload} />)}
        </div>
      )}
    </Section>
  )
}

function PrefRow({ p, onChanged }: { p: Preference; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(p.text)
  const cat = CATEGORY[p.category]
  const save = () => {
    if (!text.trim()) return
    void client.updatePreference(p.id, { text: text.trim() }).then(() => { setEditing(false); onChanged() }).catch(() => undefined)
  }
  return (
    <Row accent={cat.hex}>
      <div className="flex items-start justify-between gap-2">
        {editing ? (
          <input className={inputClass + ' min-w-0'} style={{ borderColor: 'var(--color-line-soft)' }} value={text} onChange={(e) => setText(e.target.value)} autoFocus onKeyDown={(e) => e.key === 'Enter' && save()} />
        ) : (
          <div className="min-w-0 text-[12px] text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{p.text}</div>
        )}
        <Pill color={cat.hex}>{cat.label}</Pill>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-[var(--color-cream-faint)]">
        <span>ความเชื่อมั่น {Math.round(p.confidence * 100)}%</span>
        {p.source && <span>· {p.source}</span>}
        <div className="ml-auto flex gap-1.5">
          {editing ? (
            <button type="button" onClick={save} className="rounded border px-1.5 py-0.5 text-[10px]" style={{ borderColor: withAlpha(ACCENT_HEX.amber, 0.4), color: ACCENT_HEX.amber }}>บันทึก</button>
          ) : (
            <button type="button" onClick={() => setEditing(true)} className="rounded border px-1.5 py-0.5 text-[10px] text-[var(--color-cream-dim)]" style={{ borderColor: 'var(--color-line-soft)' }}>แก้ไข</button>
          )}
          <button type="button" onClick={() => { if (confirm('ลบความชอบนี้?')) void client.deletePreference(p.id).then(onChanged).catch(() => undefined) }} className="rounded border px-1.5 py-0.5 text-[10px]" style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.4), color: ACCENT_HEX.coral }}>ลบ</button>
        </div>
      </div>
    </Row>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [category, setCategory] = useState<PreferenceCategory>('general')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (!text.trim() || busy) return
    setBusy(true)
    void client.createPreference({ category, text: text.trim(), source: 'owner' }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <Field label="ความชอบ/กฎ"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 48, resize: 'none' }} value={text} onChange={(e) => setText(e.target.value)} placeholder="เช่น ใช้โทนสุภาพเป็นกันเอง ตอบสั้นกระชับ" /></Field>
      <div className="mt-2 flex items-center justify-between">
        <div className="flex flex-wrap gap-1.5">
          {CATS.map((c) => {
            const on = category === c
            return <button key={c} type="button" onClick={() => setCategory(c)} className="rounded-lg border px-2 py-0.5 text-[10px]" style={{ borderColor: on ? withAlpha(CATEGORY[c].hex, 0.6) : 'var(--color-line-soft)', color: on ? CATEGORY[c].hex : 'var(--color-cream-faint)' }}>{CATEGORY[c].label}</button>
          })}
        </div>
        <PrimaryBtn onClick={submit} disabled={!text.trim() || busy}>บันทึก</PrimaryBtn>
      </div>
    </div>
  )
}
