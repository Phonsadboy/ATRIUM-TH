import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { LessonSource } from '../../contract/types'
import { Section, PrimaryBtn } from './shared'

const SOURCES: { id: LessonSource; label: string }[] = [
  { id: 'reject', label: 'งานถูกตีกลับ' },
  { id: 'heavy_edit', label: 'ถูกแก้หนัก' },
  { id: 'low_rating', label: 'คะแนนต่ำ' },
]

// The backend exposes POST /api/lessons (record) but no list endpoint — lessons
// feed back into department memory. This panel is a capture form.
export function LessonsPanel() {
  const [whatWentWrong, setWhat] = useState('')
  const [lesson, setLesson] = useState('')
  const [source, setSource] = useState<LessonSource>('reject')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  const submit = () => {
    if (!whatWentWrong.trim() || !lesson.trim() || busy) return
    setBusy(true)
    setDone(false)
    void client
      .createLesson({ whatWentWrong: whatWentWrong.trim(), lesson: lesson.trim(), source })
      .then(() => { setBusy(false); setDone(true); setWhat(''); setLesson('') })
      .catch(() => setBusy(false))
  }

  return (
    <Section title="บทเรียน (Lessons)" hint="บันทึกสิ่งที่ผิดพลาดเพื่อให้เอเจนต์เรียนรู้ — จะถูกป้อนกลับเข้าความจำของแผนก">
      <div className="max-w-xl space-y-3">
        <Field label="เกิดอะไรผิดพลาด">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 64, resize: 'none' }} value={whatWentWrong} onChange={(e) => setWhat(e.target.value)} placeholder="อธิบายสิ่งที่ไม่เป็นไปตามคาด" />
        </Field>
        <Field label="บทเรียนที่ได้ / ควรทำอย่างไรต่อไป">
          <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 64, resize: 'none' }} value={lesson} onChange={(e) => setLesson(e.target.value)} placeholder="สรุปแนวทางที่ถูกต้องสำหรับครั้งหน้า" />
        </Field>
        <Field label="ที่มา">
          <div className="flex flex-wrap gap-1.5">
            {SOURCES.map((s) => {
              const on = source === s.id
              return (
                <button key={s.id} type="button" onClick={() => setSource(s.id)} className="rounded-lg border px-3 py-1.5 text-[11px]" style={{ borderColor: on ? withAlpha(ACCENT_HEX.amber, 0.5) : 'var(--color-line-soft)', background: on ? withAlpha(ACCENT_HEX.amber, 0.12) : 'transparent', color: on ? ACCENT_HEX.amber : 'var(--color-cream-dim)' }}>{s.label}</button>
              )
            })}
          </div>
        </Field>
        <div className="flex items-center gap-3">
          <PrimaryBtn onClick={submit} disabled={!whatWentWrong.trim() || !lesson.trim() || busy}>บันทึกบทเรียน</PrimaryBtn>
          {done && <span className="text-[11px]" style={{ color: ACCENT_HEX.teal }}>✓ บันทึกแล้ว — ป้อนเข้าความจำของแผนกเรียบร้อย</span>}
        </div>
      </div>
    </Section>
  )
}
