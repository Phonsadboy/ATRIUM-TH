import { useState } from 'react'
import { client } from '../../state/useCompany'
import { useSelector } from '../../state/useCompany'
import { Field, Pill, Toggle, inputClass } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts } from './shared'

export function BulletinsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listBulletins({ limit: 100 }), [])
  const { label } = useDepts()
  const now = useSelector((s) => s.now)
  const [creating, setCreating] = useState(false)
  const bulletins = data ?? []

  return (
    <Section title="ประกาศ (Bulletins)" hint="กระดานข่าวกลางของบริษัท" actions={<PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ ประกาศ'}</PrimaryBtn>}>
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : bulletins.length === 0 && !error ? <Empty text="ยังไม่มีประกาศ" /> : (
        <div className="space-y-2.5">
          {bulletins.slice().sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.ts - a.ts).map((b) => (
            <Row key={b.id} accent={b.pinned ? ACCENT_HEX.amber : undefined}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{b.title}</div>
                {b.pinned && <Pill color={ACCENT_HEX.amber}>ปักหมุด</Pill>}
              </div>
              <div className="mt-1 whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-[11px] leading-relaxed text-[var(--color-cream-dim)]">{b.body}</div>
              <div className="mt-1.5 flex items-center gap-x-2 text-[10px] text-[var(--color-cream-faint)]">
                <span>โดย {label(b.author)}</span>
                <span className="ml-auto">{relTime(b.ts, now)}ที่แล้ว</span>
              </div>
            </Row>
          ))}
        </div>
      )}
    </Section>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [pinned, setPinned] = useState(false)
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (!title.trim() || !body.trim() || busy) return
    setBusy(true)
    void client.createBulletin({ title: title.trim(), body: body.trim(), author: 'owner', scope: 'company', pinned }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="หัวข้อ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="เช่น นโยบายใหม่" /></Field>
        <Field label="เนื้อหา"><textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 72, resize: 'none' }} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
        <label className="flex items-center gap-2 text-[11px] text-[var(--color-cream-dim)]"><Toggle on={pinned} onChange={setPinned} /> ปักหมุดไว้บนสุด</label>
      </div>
      <div className="mt-3 flex justify-end gap-2"><GhostBtn onClick={onDone}>ยกเลิก</GhostBtn><PrimaryBtn onClick={submit} disabled={!title.trim() || !body.trim() || busy}>ประกาศ</PrimaryBtn></div>
    </div>
  )
}
