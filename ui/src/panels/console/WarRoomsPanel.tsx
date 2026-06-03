import { useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import type { WarRoom, WarRoomStatus } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts, useNow } from './shared'

const STATUS: Record<WarRoomStatus, { label: string; hex: string }> = {
  active: { label: 'เปิดอยู่', hex: ACCENT_HEX.coral },
  resolved: { label: 'คลี่คลาย', hex: ACCENT_HEX.teal },
  archived: { label: 'เก็บคลัง', hex: ACCENT_HEX.lavender },
}

export function WarRoomsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listWarRooms({ limit: 100 }), [])
  const { label } = useDepts()
  const now = useNow()
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const rooms = data ?? []

  return (
    <Section title="วอร์รูม" hint="ห้องแก้ปัญหาเร่งด่วนแบบรวมศูนย์" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ วอร์รูม'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : rooms.length === 0 && !error ? <Empty text="ยังไม่มีวอร์รูม" /> : (
        <div className="space-y-2.5">
          {rooms.map((w) => {
            const st = STATUS[w.status]
            const open = openId === w.id
            return (
              <Row key={w.id} accent={st.hex}>
                <button type="button" onClick={() => setOpenId(open ? null : w.id)} className="block w-full text-left">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{w.title}</div>
                    <Pill color={st.hex}>{st.label}</Pill>
                  </div>
                  <div className="mt-1 line-clamp-2 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{w.goal}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-[var(--color-cream-faint)]">
                    {w.members.length > 0 && <span>{w.members.map(label).join(' · ')}</span>}
                    <span className="ml-auto">{relTime(w.createdAt, now)}ที่แล้ว</span>
                  </div>
                </button>
                {open && <Detail w={w} onChanged={reload} />}
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}

function Detail({ w, onChanged }: { w: WarRoom; onChanged: () => void }) {
  const [scratch, setScratch] = useState(w.scratchpad)
  const setStatus = (status: WarRoomStatus) => void client.updateWarRoom(w.id, { status }).then(onChanged).catch(() => undefined)
  const saveScratch = () => void client.updateWarRoom(w.id, { scratchpad: scratch }).then(onChanged).catch(() => undefined)
  return (
    <div className="mt-3 space-y-2 border-t pt-3" style={{ borderColor: 'var(--color-line-soft)' }}>
      <Field label="กระดานทด (scratchpad)">
        <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 70, resize: 'vertical' }} value={scratch} onChange={(e) => setScratch(e.target.value)} />
      </Field>
      {w.decisions.length > 0 && <div className="text-[11px]" style={{ color: ACCENT_HEX.teal }}>มติ: {w.decisions.join(' · ')}</div>}
      {w.artifacts.length > 0 && <div className="text-[11px] text-[var(--color-cream-faint)]">ชิ้นงาน: {w.artifacts.join(' · ')}</div>}
      <div className="flex items-center justify-between">
        <div className="flex flex-wrap items-center gap-1.5">
          {(Object.keys(STATUS) as WarRoomStatus[]).map((s) => (
            <button key={s} type="button" disabled={s === w.status} onClick={() => setStatus(s)} className="rounded-lg border px-2 py-0.5 text-[10px] disabled:opacity-40" style={{ borderColor: withAlpha(STATUS[s].hex, 0.4), color: STATUS[s].hex }}>{STATUS[s].label}</button>
          ))}
        </div>
        <GhostBtn onClick={saveScratch} tint={ACCENT_HEX.amber}>บันทึกกระดาน</GhostBtn>
      </div>
    </div>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [members, setMembers] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const toggle = (id: string) => setMembers((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))
  const submit = () => {
    if (!title.trim() || !goal.trim() || busy) return
    setBusy(true)
    void client.createWarRoom({ title: title.trim(), goal: goal.trim(), members }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="ชื่อวอร์รูม"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="เช่น แก้บั๊กระบบจ่ายเงิน" /></Field>
        <Field label="เป้าหมาย/ปัญหา"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 56, resize: 'none' }} value={goal} onChange={(e) => setGoal(e.target.value)} /></Field>
        <Field label="สมาชิก">
          <div className="flex flex-wrap gap-1.5">
            {depts.map((d) => {
              const on = members.includes(d.id)
              return <button key={d.id} type="button" onClick={() => toggle(d.id)} className="rounded-lg border px-2 py-1 text-[11px]" style={{ borderColor: on ? withAlpha(ACCENT_HEX.teal, 0.5) : 'var(--color-line-soft)', background: on ? withAlpha(ACCENT_HEX.teal, 0.12) : 'transparent', color: on ? ACCENT_HEX.teal : 'var(--color-cream-dim)' }}>{d.emoji} {d.name}</button>
            })}
          </div>
        </Field>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!title.trim() || !goal.trim() || busy}>เปิดวอร์รูม</PrimaryBtn></div>
    </div>
  )
}
