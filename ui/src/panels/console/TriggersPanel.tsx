import { useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, Toggle, inputClass } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { clockLabel } from '../../lib/format'
import type { Trigger, TriggerKind, TriggerEvent } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts } from './shared'

const EVENTS: { id: TriggerEvent; label: string }[] = [
  { id: 'dept_done', label: 'แผนกทำงานเสร็จ' },
  { id: 'blocked', label: 'เจอทางตัน' },
  { id: 'idle', label: 'ว่างนาน' },
  { id: 'budget', label: 'งบใกล้หมด' },
  { id: 'escalate', label: 'ต้องยกระดับ' },
]
const EVENT_LABEL = Object.fromEntries(EVENTS.map((e) => [e.id, e.label])) as Record<TriggerEvent, string>

export function TriggersPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listTriggers({ limit: 100 }), [])
  const { label } = useDepts()
  const [creating, setCreating] = useState(false)
  const triggers = data ?? []

  return (
    <Section title="ตารางเวลา & ทริกเกอร์" hint="สั่งงานตามรอบเวลา (cron) หรือเมื่อเกิดเหตุการณ์" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ ทริกเกอร์'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : triggers.length === 0 && !error ? <Empty text="ยังไม่มีทริกเกอร์" /> : (
        <div className="space-y-2.5">
          {triggers.map((t: Trigger) => (
            <Row key={t.id} accent={t.enabled ? ACCENT_HEX.teal : undefined}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{t.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
                    <Pill color={t.kind === 'cron' ? ACCENT_HEX.sky : ACCENT_HEX.lavender}>{t.kind === 'cron' ? 'ตามเวลา' : 'ตามเหตุการณ์'}</Pill>
                    {t.kind === 'cron' ? <span style={{ fontFamily: 'var(--font-mono)' }}>{t.cadence}</span> : <span>{t.event ? EVENT_LABEL[t.event] : ''}</span>}
                    <span>→ {label(t.target)}</span>
                    {t.nextRunAt && <span>· ครั้งถัดไป {clockLabel(t.nextRunAt)}</span>}
                  </div>
                </div>
                <Toggle on={t.enabled} onChange={(on) => void client.updateTrigger(t.id, { enabled: on }).then(reload).catch(() => undefined)} />
              </div>
            </Row>
          ))}
        </div>
      )}
    </Section>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [kind, setKind] = useState<TriggerKind>('cron')
  const [title, setTitle] = useState('')
  const [target, setTarget] = useState(depts[0]?.id ?? '')
  const [cadence, setCadence] = useState('0 9 * * *')
  const [event, setEvent] = useState<TriggerEvent>('dept_done')
  const [busy, setBusy] = useState(false)

  const submit = () => {
    if (!title.trim() || !target || busy) return
    setBusy(true)
    void client.createTrigger({
      kind,
      title: title.trim(),
      target,
      cadence: kind === 'cron' ? cadence.trim() : null,
      event: kind === 'event' ? event : null,
      enabled: true,
    }).then(onDone).catch(() => setBusy(false))
  }

  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <div className="flex gap-1 rounded-xl p-1" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}>
          {(['cron', 'event'] as TriggerKind[]).map((k) => {
            const on = kind === k
            return <button key={k} type="button" onClick={() => setKind(k)} className="flex-1 rounded-lg px-2 py-1.5 text-[11px] font-medium" style={{ background: on ? ACCENT_HEX.amber : 'transparent', color: on ? '#1a1610' : 'var(--color-cream-dim)' }}>{k === 'cron' ? 'ตามเวลา (cron)' : 'ตามเหตุการณ์'}</button>
          })}
        </div>
        <Field label="ชื่อ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="เช่น สรุปยอดทุกเช้า" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="สั่งงานแผนก">
            <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={target} onChange={(e) => setTarget(e.target.value)}>
              {depts.map((d) => <option key={d.id} value={d.id}>{d.emoji} {d.name}</option>)}
            </select>
          </Field>
          {kind === 'cron' ? (
            <Field label="cron (นาที ชม. วัน เดือน วันสัปดาห์)"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)', fontFamily: 'var(--font-mono)' }} value={cadence} onChange={(e) => setCadence(e.target.value)} placeholder="0 9 * * *" /></Field>
          ) : (
            <Field label="เหตุการณ์">
              <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={event} onChange={(e) => setEvent(e.target.value as TriggerEvent)}>
                {EVENTS.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
              </select>
            </Field>
          )}
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!title.trim() || !target || busy}>สร้างทริกเกอร์</PrimaryBtn></div>
    </div>
  )
}
