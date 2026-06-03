import { useState } from 'react'
import { client } from '../../state/useCompany'
import { Field, inputClass, withAlpha, Toggle } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { NotifChannel, NotifType, NotificationPreferences } from '../../contract/types'
import { Section, Loading, ErrorNote, PrimaryBtn, useAsync } from './shared'

const TYPE_LABEL: Record<NotifType, string> = {
  approval: 'การอนุมัติ', budget: 'งบประมาณ', blocked: 'งานติดขัด', task_done: 'งานเสร็จ',
  digest: 'สรุปรวม', crash: 'ระบบล่ม', knowledge_debt: 'หนี้ความรู้', security: 'ความปลอดภัย',
}
const TYPES = Object.keys(TYPE_LABEL) as NotifType[]
const CHANNELS: { id: NotifChannel; label: string }[] = [
  { id: 'off', label: 'ปิด' },
  { id: 'inbox', label: 'กล่องข้อความ' },
  { id: 'push', label: 'แจ้งเตือนด่วน' },
]

export function NotificationPrefsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.getNotificationPreferences(), [])

  return (
    <Section title="ตั้งค่าการแจ้งเตือน" hint="เลือกช่องทางแจ้งเตือนแต่ละประเภท และช่วงเวลางดรบกวน">
      {error ? (
        <ErrorNote error={`${error} — ฟีเจอร์นี้อาจยังไม่พร้อมบน backend เวอร์ชันนี้`} onRetry={reload} />
      ) : loading && !data ? (
        <Loading />
      ) : data ? (
        // key on updatedAt so the form re-seeds cleanly when the backend value changes
        <PrefsForm key={String(data.updatedAt)} initial={data} onSaved={reload} />
      ) : null}
    </Section>
  )
}

function PrefsForm({ initial, onSaved }: { initial: NotificationPreferences; onSaved: () => void }) {
  const [byType, setByType] = useState<Partial<Record<NotifType, NotifChannel>>>(initial.byType ?? {})
  const [quiet, setQuiet] = useState<NotificationPreferences['quietHours']>(initial.quietHours ?? {})
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  const save = () => {
    setBusy(true)
    setSaved(false)
    void client
      .updateNotificationPreferences({ byType, quietHours: quiet })
      .then(() => { setBusy(false); setSaved(true); onSaved() })
      .catch(() => setBusy(false))
  }

  return (
    <div className="max-w-xl space-y-4">
      <div className="space-y-2">
        {TYPES.map((t) => (
          <div key={t} className="flex items-center justify-between gap-3 rounded-xl border p-2.5" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
            <span className="text-[12px] text-[var(--color-cream)]">{TYPE_LABEL[t]}</span>
            <div className="flex gap-1">
              {CHANNELS.map((c) => {
                const on = (byType[t] ?? 'inbox') === c.id
                return (
                  <button key={c.id} type="button" onClick={() => setByType((cur) => ({ ...cur, [t]: c.id }))} className="rounded-lg border px-2 py-1 text-[10px] transition-colors" style={{ borderColor: on ? withAlpha(ACCENT_HEX.amber, 0.55) : 'var(--color-line-soft)', background: on ? withAlpha(ACCENT_HEX.amber, 0.12) : 'transparent', color: on ? ACCENT_HEX.amber : 'var(--color-cream-faint)' }}>{c.label}</button>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-xl border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
        <label className="flex items-center justify-between">
          <span className="text-[12px] font-medium text-[var(--color-cream)]">ช่วงงดรบกวน (Quiet hours)</span>
          <Toggle on={!!quiet.enabled} onChange={(on) => setQuiet((q) => ({ ...q, enabled: on }))} />
        </label>
        {quiet.enabled && (
          <div className="mt-2 grid grid-cols-2 gap-3">
            <Field label="เริ่ม"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={quiet.start ?? ''} onChange={(e) => setQuiet((q) => ({ ...q, start: e.target.value }))} placeholder="22:00" /></Field>
            <Field label="สิ้นสุด"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={quiet.end ?? ''} onChange={(e) => setQuiet((q) => ({ ...q, end: e.target.value }))} placeholder="07:00" /></Field>
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <PrimaryBtn onClick={save} disabled={busy}>บันทึกการตั้งค่า</PrimaryBtn>
        {saved && <span className="text-[11px]" style={{ color: ACCENT_HEX.teal }}>✓ บันทึกแล้ว</span>}
      </div>
    </div>
  )
}
