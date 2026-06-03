import { useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX, SEVERITY_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import type { AuditKind, AuditLogEntry, Severity } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, PrimaryBtn, useAsync, useDepts } from './shared'

const KIND_LABEL: Record<AuditKind, string> = {
  activity: 'กิจกรรม', approval: 'การอนุมัติ', tool_run: 'รันเครื่องมือ', note: 'บันทึก',
}
const KIND_HEX: Record<AuditKind, string> = {
  activity: ACCENT_HEX.sky, approval: ACCENT_HEX.amber, tool_run: ACCENT_HEX.lavender, note: ACCENT_HEX.teal,
}
const KINDS: (AuditKind | '')[] = ['', 'activity', 'approval', 'tool_run', 'note']

export function AuditPanel() {
  const [kind, setKind] = useState<AuditKind | ''>('')
  const { data, loading, error, reload } = useAsync(
    () => client.listAuditLogs({ kind: kind || undefined, limit: 150 }),
    [kind],
  )
  const { label } = useDepts()
  const now = useSelector((s) => s.now)
  const [adding, setAdding] = useState(false)
  const logs = data ?? []

  return (
    <Section
      title="บันทึกตรวจสอบ (Audit)"
      hint="รอยกิจกรรม การอนุมัติ การรันเครื่องมือ และบันทึกย่อ"
      actions={
        <>
          <select value={kind} onChange={(e) => setKind(e.target.value as AuditKind | '')} className="rounded-lg border bg-[var(--color-surface)] px-2 py-1.5 text-[11px] text-[var(--color-cream)] outline-none" style={{ borderColor: 'var(--color-line-soft)' }}>
            {KINDS.map((k) => <option key={k || 'all'} value={k}>{k ? KIND_LABEL[k] : 'ทุกประเภท'}</option>)}
          </select>
          <PrimaryBtn onClick={() => setAdding((v) => !v)}>{adding ? 'ปิด' : '+ บันทึกย่อ'}</PrimaryBtn>
        </>
      }
    >
      {adding && <NoteForm onDone={() => { setAdding(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : logs.length === 0 && !error ? <Empty text="ไม่มีรายการ" /> : (
        <div className="space-y-2">
          {logs.map((e: AuditLogEntry) => {
            const sev = SEVERITY_HEX[e.severity] ?? ACCENT_HEX.lavender
            return (
              <div key={e.id} className="rounded-xl border p-2.5" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{e.title}</div>
                  <Pill color={KIND_HEX[e.kind]}>{KIND_LABEL[e.kind]}</Pill>
                </div>
                {e.detail && <div className="mt-0.5 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{e.detail}</div>}
                <div className="mt-1 flex items-center gap-x-2 text-[10px] text-[var(--color-cream-faint)]">
                  <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: sev }} />{e.severity}</span>
                  {e.departmentId && <span>· {label(e.departmentId)}</span>}
                  {e.author && <span>· {label(e.author)}</span>}
                  <span className="ml-auto">{relTime(e.ts, now)}ที่แล้ว</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Section>
  )
}

function NoteForm({ onDone }: { onDone: () => void }) {
  const [body, setBody] = useState('')
  const [severity, setSeverity] = useState<Severity>('info')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (!body.trim() || busy) return
    setBusy(true)
    void client.createAuditNote({ body: body.trim(), severity, author: 'owner' }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <Field label="บันทึกย่อ"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 56, resize: 'none' }} value={body} onChange={(e) => setBody(e.target.value)} placeholder="ข้อความที่ต้องการบันทึกไว้ในรอยตรวจสอบ" /></Field>
      <div className="mt-2 flex items-center justify-between">
        <div className="flex gap-1.5">
          {(['info', 'good', 'warn', 'alert'] as Severity[]).map((s) => {
            const on = severity === s
            return <button key={s} type="button" onClick={() => setSeverity(s)} className="rounded-lg border px-2 py-0.5 text-[10px]" style={{ borderColor: on ? withAlpha(SEVERITY_HEX[s], 0.6) : 'var(--color-line-soft)', color: on ? SEVERITY_HEX[s] : 'var(--color-cream-faint)' }}>{s}</button>
          })}
        </div>
        <PrimaryBtn onClick={submit} disabled={!body.trim() || busy}>บันทึก</PrimaryBtn>
      </div>
    </div>
  )
}
