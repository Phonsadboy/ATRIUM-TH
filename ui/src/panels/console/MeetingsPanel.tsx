import { useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import type { Meeting, MeetingStatus } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts, useNow } from './shared'

const STATUS: Record<MeetingStatus, { label: string; hex: string }> = {
  scheduled: { label: 'นัดไว้', hex: ACCENT_HEX.sky },
  active: { label: 'กำลังประชุม', hex: ACCENT_HEX.teal },
  done: { label: 'จบแล้ว', hex: ACCENT_HEX.lavender },
}

export function MeetingsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listMeetings({ limit: 100 }), [])
  const { label } = useDepts()
  const now = useNow()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const meetings = data ?? []

  return (
    <Section title="การประชุม" hint="วาระ · ผู้เข้าร่วม · บันทึก · สิ่งที่ต้องทำต่อ" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ ประชุมใหม่'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : meetings.length === 0 && !error ? <Empty text="ยังไม่มีการประชุม" /> : (
        <div className="space-y-2.5">
          {meetings.map((m) => {
            const st = STATUS[m.status]
            const open = openId === m.id
            return (
              <Row key={m.id} accent={st.hex}>
                <button type="button" onClick={() => setOpenId(open ? null : m.id)} className="block w-full text-left">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{m.title}</div>
                    <Pill color={st.hex}>{st.label}</Pill>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-[var(--color-cream-faint)]">
                    {m.participants.length > 0 && <span>{m.participants.map(label).join(' · ')}</span>}
                    <span className="ml-auto">{relTime(m.ts, now)}ที่แล้ว</span>
                  </div>
                </button>
                {open && <Detail m={m} onChanged={reload} />}
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}

function Detail({ m, onChanged }: { m: Meeting; onChanged: () => void }) {
  const [note, setNote] = useState('')
  const setStatus = (status: MeetingStatus) => void client.updateMeeting(m.id, { status }).then(onChanged).catch(() => undefined)
  const addNote = () => {
    if (!note.trim()) return
    const next = m.notes ? `${m.notes}\n${note.trim()}` : note.trim()
    void client.updateMeeting(m.id, { notes: next }).then(() => { setNote(''); onChanged() }).catch(() => undefined)
  }
  return (
    <div className="mt-3 space-y-2 border-t pt-3" style={{ borderColor: 'var(--color-line-soft)' }}>
      {m.agenda.length > 0 && (
        <div className="text-[11px] text-[var(--color-cream-dim)]">
          <span className="text-[var(--color-cream-faint)]">วาระ:</span>
          <ul className="mt-0.5 list-disc pl-4">{m.agenda.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      {m.notes && <div className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] rounded-lg p-2 text-[11px] text-[var(--color-cream-dim)]" style={{ background: 'var(--color-surface)' }}>{m.notes}</div>}
      {m.actionItems.length > 0 && (
        <div className="text-[11px] text-[var(--color-cream-dim)]"><span className="text-[var(--color-cream-faint)]">สิ่งที่ต้องทำ:</span> {m.actionItems.join(' · ')}</div>
      )}
      {m.decisions.length > 0 && (
        <div className="text-[11px]" style={{ color: ACCENT_HEX.teal }}>มติ: {m.decisions.join(' · ')}</div>
      )}
      <div className="flex items-center gap-2">
        <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="เพิ่มบันทึก…" onKeyDown={(e) => e.key === 'Enter' && addNote()} />
        <GhostBtn onClick={addNote} tint={ACCENT_HEX.amber}>เพิ่ม</GhostBtn>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[10px] text-[var(--color-cream-faint)]">สถานะ:</span>
        {(Object.keys(STATUS) as MeetingStatus[]).map((s) => (
          <button key={s} type="button" disabled={s === m.status} onClick={() => setStatus(s)} className="rounded-lg border px-2 py-0.5 text-[10px] disabled:opacity-40" style={{ borderColor: withAlpha(STATUS[s].hex, 0.4), color: STATUS[s].hex }}>{STATUS[s].label}</button>
        ))}
      </div>
    </div>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [title, setTitle] = useState('')
  const [agenda, setAgenda] = useState('')
  const [participants, setParticipants] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const toggle = (id: string) => setParticipants((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))
  const submit = () => {
    if (!title.trim() || busy) return
    setBusy(true)
    void client.createMeeting({
      title: title.trim(),
      agenda: agenda.split('\n').map((l) => l.trim()).filter(Boolean),
      participants,
    }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="หัวข้อ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="เช่น รีวิวสัปดาห์" /></Field>
        <Field label="วาระ (บรรทัดละข้อ)"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 56, resize: 'none' }} value={agenda} onChange={(e) => setAgenda(e.target.value)} placeholder={'อัปเดตงาน\nปัญหาที่ติด'} /></Field>
        <Field label="ผู้เข้าร่วม">
          <div className="flex flex-wrap gap-1.5">
            {depts.map((d) => {
              const on = participants.includes(d.id)
              return <button key={d.id} type="button" onClick={() => toggle(d.id)} className="rounded-lg border px-2 py-1 text-[11px]" style={{ borderColor: on ? withAlpha(ACCENT_HEX.teal, 0.5) : 'var(--color-line-soft)', background: on ? withAlpha(ACCENT_HEX.teal, 0.12) : 'transparent', color: on ? ACCENT_HEX.teal : 'var(--color-cream-dim)' }}>{d.emoji} {d.name}</button>
            })}
          </div>
        </Field>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!title.trim() || busy}>สร้าง</PrimaryBtn></div>
    </div>
  )
}
