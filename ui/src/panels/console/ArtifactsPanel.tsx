import { useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, Toggle, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { isExec } from '../../lib/threads'
import type {
  ArtifactKind,
  ArtifactStatus,
  ArtifactVersion,
  ArtifactContentResponse,
  ArtifactDiffResponse,
  ArtifactPreviewResponse,
  CritiqueReport,
} from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, useAsync, useDepts, useNow } from './shared'

const STATUS: Record<ArtifactStatus, { label: string; hex: string }> = {
  draft: { label: 'ร่าง', hex: ACCENT_HEX.lavender },
  in_review: { label: 'กำลังตรวจ', hex: ACCENT_HEX.amber },
  approved: { label: 'อนุมัติ', hex: ACCENT_HEX.teal },
  published: { label: 'เผยแพร่', hex: ACCENT_HEX.sky },
  superseded: { label: 'ถูกแทนที่', hex: ACCENT_HEX.honey },
  archived: { label: 'เก็บคลัง', hex: ACCENT_HEX.coral },
}

const KIND_LABEL: Record<ArtifactKind, string> = {
  file: 'ไฟล์', doc: 'เอกสาร', code: 'โค้ด', report: 'รายงาน',
  link: 'ลิงก์', memo: 'บันทึก', dataset: 'ชุดข้อมูล', image: 'รูปภาพ',
}
const KINDS = Object.keys(KIND_LABEL) as ArtifactKind[]
const STATUSES = Object.keys(STATUS) as ArtifactStatus[]

export function ArtifactsPanel() {
  const [statusFilter, setStatusFilter] = useState<ArtifactStatus | ''>('')
  const { data, loading, error, reload } = useAsync(
    () => client.listArtifacts({ status: statusFilter || undefined, limit: 200 }),
    [statusFilter],
  )
  const { label } = useDepts()
  const now = useNow()
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)

  if (detailId) return <ArtifactDetail id={detailId} onBack={() => { setDetailId(null); reload() }} deptLabel={label} />

  const artifacts = data ?? []

  return (
    <Section
      title="ชิ้นงาน (Artifacts)"
      hint="ผลงานที่มีเวอร์ชัน · เทียบ diff · ย้อนเวอร์ชัน"
      actions={
        <>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as ArtifactStatus | '')}
            className="rounded-lg border bg-[var(--color-surface)] px-2 py-1.5 text-[11px] text-[var(--color-cream)] outline-none"
            style={{ borderColor: 'var(--color-line-soft)' }}
          >
            <option value="">ทุกสถานะ</option>
            {STATUSES.map((s) => <option key={s} value={s}>{STATUS[s].label}</option>)}
          </select>
          <GhostBtn onClick={() => setImporting((v) => !v)}>{importing ? 'ปิด' : 'นำเข้าไฟล์'}</GhostBtn>
          <PrimaryBtn onClick={() => setCreating((v) => !v)}>{creating ? 'ปิดฟอร์ม' : '+ ชิ้นงาน'}</PrimaryBtn>
        </>
      }
    >
      {importing && <ImportForm onDone={() => { setImporting(false); reload() }} />}
      {creating && <CreateForm onDone={() => { setCreating(false); reload() }} />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : artifacts.length === 0 && !error ? (
        <Empty text="ยังไม่มีชิ้นงาน" />
      ) : (
        <div className="space-y-2.5">
          {artifacts.map((a) => {
            const st = STATUS[a.status]
            return (
              <Row key={a.id} accent={st.hex} onClick={() => setDetailId(a.id)}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{a.name}</div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className="text-[10px] tabular-nums text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>v{a.version}</span>
                    <Pill color={st.hex}>{st.label}</Pill>
                  </div>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
                  <Pill color={ACCENT_HEX.sky}>{KIND_LABEL[a.kind]}</Pill>
                  <span>{label(a.ownerDept)}</span>
                  {a.tags.slice(0, 3).map((t) => <span key={t}>#{t}</span>)}
                  <span className="ml-auto">{relTime(a.updatedAt, now)}ที่แล้ว</span>
                </div>
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}

function ArtifactDetail({ id, onBack, deptLabel }: { id: string; onBack: () => void; deptLabel: (id: string | null | undefined) => string }) {
  const { data, loading, error, reload } = useAsync(() => client.getArtifactContent(id), [id])
  const versionsQ = useAsync(() => client.listArtifactVersions(id), [id])
  const [editing, setEditing] = useState(false)

  const content: ArtifactContentResponse | null = data
  const art = content?.artifact ?? null

  return (
    <Section
      title={art ? art.name : 'ชิ้นงาน'}
      hint={art ? `${KIND_LABEL[art.kind]} · v${art.version} · ${deptLabel(art.ownerDept)}` : undefined}
      actions={<GhostBtn onClick={onBack}>← กลับ</GhostBtn>}
    >
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : art ? (
        <div className="space-y-3">
          {/* status controls */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[10px] text-[var(--color-cream-faint)]">สถานะ:</span>
            {STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                disabled={s === art.status}
                onClick={() => void client.updateArtifact(id, { status: s }).then(reload).catch(() => undefined)}
                className="rounded-lg border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40"
                style={{ borderColor: withAlpha(STATUS[s].hex, 0.4), color: STATUS[s].hex }}
              >
                {STATUS[s].label}
              </button>
            ))}
          </div>

          {/* content */}
          <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-semibold text-[var(--color-cream-dim)]">เนื้อหา (v{content?.version.version})</span>
              <GhostBtn onClick={() => setEditing((v) => !v)} tint={ACCENT_HEX.amber}>{editing ? 'ยกเลิก' : 'บันทึกเวอร์ชันใหม่'}</GhostBtn>
            </div>
            {editing ? (
              <NewVersionForm id={id} initial={content?.content ?? ''} onDone={() => { setEditing(false); reload(); versionsQ.reload() }} />
            ) : (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg p-2 text-[12px] leading-relaxed text-[var(--color-cream)]" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)' }}>
                {content?.content || <span className="text-[var(--color-cream-faint)]">— ว่าง —</span>}
              </pre>
            )}
          </div>

          {/* rendered preview (on demand) */}
          <ArtifactPreviewBlock id={id} />

          {/* versions */}
          <Versions id={id} versions={versionsQ.data ?? []} current={art.version} onChanged={() => { reload(); versionsQ.reload() }} />

          {/* provenance + red-team */}
          <EvidenceAndCritique artifactId={id} />
        </div>
      ) : (
        <Empty text="ไม่พบชิ้นงาน" />
      )}
    </Section>
  )
}

function ArtifactPreviewBlock({ id }: { id: string }) {
  const [data, setData] = useState<ArtifactPreviewResponse | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle')
  const load = () => {
    setState('loading')
    void client.getArtifactPreview(id).then((d) => { setData(d); setState('idle') }).catch(() => setState('error'))
  }
  const isImage = data && (data.preview.kind === 'image' || data.preview.kind === 'screenshot')
  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold text-[var(--color-cream-dim)]">ตัวอย่าง (render)</span>
        <GhostBtn onClick={load} tint={ACCENT_HEX.sky}>{state === 'loading' ? 'กำลังโหลด…' : data ? 'โหลดใหม่' : 'โหลดตัวอย่าง'}</GhostBtn>
      </div>
      {state === 'error' && <div className="mt-2 text-[11px] text-[var(--color-cream-faint)]">ยังเรียกตัวอย่างไม่ได้ (อาจยังไม่พร้อมบน backend นี้)</div>}
      {data && (
        <div className="mt-2">
          <div className="mb-1 text-[10px] text-[var(--color-cream-faint)]">ชนิด: {data.preview.kind} · v{data.version}</div>
          {isImage ? (
            <img src={data.preview.uri} alt="preview" className="max-h-72 rounded-lg" />
          ) : data.content ? (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg p-2 text-[12px]" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>{data.content}</pre>
          ) : (
            <a href={data.preview.uri} target="_blank" rel="noreferrer" className="text-[12px] underline break-all" style={{ color: ACCENT_HEX.sky }}>{data.preview.uri}</a>
          )}
        </div>
      )}
    </div>
  )
}

function EvidenceAndCritique({ artifactId }: { artifactId: string }) {
  const pack = useAsync(() => client.getEvidencePack(artifactId), [artifactId])
  const [critique, setCritique] = useState<CritiqueReport | null>(null)
  const [critiqueBusy, setCritiqueBusy] = useState(false)
  const [creatingPack, setCreatingPack] = useState(false)

  const runCritique = () => {
    setCritiqueBusy(true)
    void client
      .createCritique({ targetType: 'artifact', targetId: artifactId })
      .then((r) => { setCritique(r); setCritiqueBusy(false) })
      .catch(() => setCritiqueBusy(false))
  }

  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-[var(--color-cream-dim)]">หลักฐาน & การวิจารณ์</span>
        <div className="flex gap-1.5">
          {!pack.data && !pack.loading && <GhostBtn onClick={() => setCreatingPack((v) => !v)} tint={ACCENT_HEX.teal}>{creatingPack ? 'ปิด' : '+ หลักฐาน'}</GhostBtn>}
          <GhostBtn onClick={runCritique} tint={ACCENT_HEX.coral}>{critiqueBusy ? 'กำลังวิเคราะห์…' : 'ขอบทวิจารณ์'}</GhostBtn>
        </div>
      </div>

      {creatingPack && <EvidenceForm artifactId={artifactId} onDone={() => { setCreatingPack(false); pack.reload() }} />}

      {pack.data ? (
        <div className="space-y-1.5 text-[11px] text-[var(--color-cream-dim)]">
          <div>ความเชื่อมั่น: <span style={{ color: ACCENT_HEX.teal }}>{Math.round((pack.data.confidence ?? 0) * 100)}%</span></div>
          {pack.data.methodology && <div>วิธีการ: {pack.data.methodology}</div>}
          {pack.data.citations.length > 0 && (
            <div>
              <span className="text-[var(--color-cream-faint)]">อ้างอิง:</span>
              <ul className="mt-0.5 list-disc pl-4">{pack.data.citations.map((c, i) => <li key={i}>{c.source}{c.quote ? ` — “${c.quote}”` : ''}</li>)}</ul>
            </div>
          )}
          {pack.data.assumptions.length > 0 && <div>สมมติฐาน: {pack.data.assumptions.join(' · ')}</div>}
          {pack.data.gaps.length > 0 && <div style={{ color: ACCENT_HEX.amber }}>ช่องว่าง: {pack.data.gaps.join(' · ')}</div>}
        </div>
      ) : !creatingPack ? (
        <div className="text-[11px] text-[var(--color-cream-faint)]">ยังไม่มีชุดหลักฐานสำหรับชิ้นงานนี้</div>
      ) : null}

      {critique && (
        <div className="mt-3 space-y-1.5 rounded-xl border p-2.5 text-[11px]" style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.3), background: withAlpha(ACCENT_HEX.coral, 0.06) }}>
          <div className="font-semibold" style={{ color: ACCENT_HEX.coral }}>บทวิจารณ์ (red-team)</div>
          {critique.risks.length > 0 && <div className="text-[var(--color-cream-dim)]">ความเสี่ยง: {critique.risks.join(' · ')}</div>}
          {critique.untestedAssumptions.length > 0 && <div className="text-[var(--color-cream-dim)]">สมมติฐานที่ยังไม่พิสูจน์: {critique.untestedAssumptions.join(' · ')}</div>}
          {critique.missedAlternatives.length > 0 && <div className="text-[var(--color-cream-dim)]">ทางเลือกที่มองข้าม: {critique.missedAlternatives.join(' · ')}</div>}
          {critique.openQuestions.length > 0 && <div className="text-[var(--color-cream-dim)]">คำถามที่ยังเปิดอยู่: {critique.openQuestions.join(' · ')}</div>}
        </div>
      )}
    </div>
  )
}

function EvidenceForm({ artifactId, onDone }: { artifactId: string; onDone: () => void }) {
  const [methodology, setMethodology] = useState('')
  const [confidence, setConfidence] = useState('0.7')
  const [source, setSource] = useState('')
  const [quote, setQuote] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (busy) return
    setBusy(true)
    void client
      .createEvidencePack({
        artifactId,
        methodology: methodology.trim() || null,
        confidence: Number(confidence) || 0,
        citations: source.trim() ? [{ source: source.trim(), quote: quote.trim() }] : [],
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }
  return (
    <div className="mb-2 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <Field label="วิธีการ"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={methodology} onChange={(e) => setMethodology(e.target.value)} /></Field>
        <Field label="ความเชื่อมั่น (0-1)"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={confidence} onChange={(e) => setConfidence(e.target.value)} /></Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="แหล่งอ้างอิง"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={source} onChange={(e) => setSource(e.target.value)} /></Field>
        <Field label="ข้อความอ้างอิง"><input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={quote} onChange={(e) => setQuote(e.target.value)} /></Field>
      </div>
      <div className="flex justify-end"><PrimaryBtn onClick={submit} disabled={busy}>บันทึกหลักฐาน</PrimaryBtn></div>
    </div>
  )
}

function NewVersionForm({ id, initial, onDone }: { id: string; initial: string; onDone: () => void }) {
  const [text, setText] = useState(initial)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = () => {
    if (busy) return
    setBusy(true)
    void client.putArtifactContent(id, { text, note: note.trim() || 'อัปเดตเนื้อหา' }).then(onDone).catch(() => setBusy(false))
  }
  return (
    <div className="space-y-2">
      <textarea className={inputClass} style={{ borderColor: 'var(--color-line-soft)', minHeight: 140, resize: 'vertical', fontFamily: 'var(--font-mono)' }} value={text} onChange={(e) => setText(e.target.value)} />
      <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="โน้ตเวอร์ชัน (เช่น แก้บทนำ)" />
      <div className="flex justify-end"><PrimaryBtn onClick={submit} disabled={busy}>บันทึกเวอร์ชัน</PrimaryBtn></div>
    </div>
  )
}

function Versions({ id, versions, current, onChanged }: { id: string; versions: ArtifactVersion[]; current: number; onChanged: () => void }) {
  const now = useNow()
  const [diff, setDiff] = useState<ArtifactDiffResponse | null>(null)
  const [diffBusy, setDiffBusy] = useState(false)

  if (versions.length === 0) return null

  const showDiff = (from: number) => {
    setDiffBusy(true)
    void client.getArtifactDiff(id, from, current).then((d) => { setDiff(d); setDiffBusy(false) }).catch(() => setDiffBusy(false))
  }

  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="mb-2 text-[11px] font-semibold text-[var(--color-cream-dim)]">ประวัติเวอร์ชัน ({versions.length})</div>
      <div className="space-y-1.5">
        {versions.slice().sort((a, b) => b.version - a.version).map((v) => (
          <div key={v.version} className="flex items-center gap-2 text-[11px]">
            <span className="tabular-nums text-[var(--color-cream)]" style={{ fontFamily: 'var(--font-mono)' }}>v{v.version}</span>
            <span className="truncate text-[var(--color-cream-dim)]">{v.note}</span>
            <span className="ml-auto shrink-0 text-[10px] text-[var(--color-cream-faint)]">{relTime(v.ts, now)}ที่แล้ว</span>
            {v.version !== current && (
              <>
                <button type="button" onClick={() => showDiff(v.version)} className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-[var(--color-cream-dim)]" style={{ borderColor: 'var(--color-line-soft)' }}>diff</button>
                <button
                  type="button"
                  onClick={() => { if (confirm(`ย้อนกลับไปเวอร์ชัน v${v.version}?`)) void client.rollbackArtifact(id, { version: v.version }).then(onChanged).catch(() => undefined) }}
                  className="shrink-0 rounded border px-1.5 py-0.5 text-[10px]"
                  style={{ borderColor: withAlpha(ACCENT_HEX.amber, 0.4), color: ACCENT_HEX.amber }}
                >
                  ย้อน
                </button>
              </>
            )}
          </div>
        ))}
      </div>
      {diffBusy && <div className="mt-2 text-[11px] text-[var(--color-cream-faint)]">กำลังเทียบ…</div>}
      {diff && (
        <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg p-2 text-[11px] leading-relaxed" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>
          {diff.diff || '— ไม่มีความต่าง —'}
        </pre>
      )}
    </div>
  )
}

function ImportForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.filter((d) => !isExec(d.id)).map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [sourcePath, setSourcePath] = useState('')
  const [targetDept, setTargetDept] = useState(depts[0]?.id ?? '')
  const [artifactName, setArtifactName] = useState('')
  const [tags, setTags] = useState('')
  const [copyToWorkspace, setCopy] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = () => {
    if (!sourcePath.trim() || !targetDept || busy) return
    setBusy(true); setErr(null)
    void client
      .importFile({
        sourcePath: sourcePath.trim(),
        targetDept,
        artifactName: artifactName.trim() || null,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        copyToWorkspace,
      })
      .then(onDone)
      .catch((e: unknown) => { setBusy(false); setErr(e instanceof Error ? e.message : String(e)) })
  }

  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="mb-2 text-[11px] font-semibold text-[var(--color-cream-dim)]">นำเข้าไฟล์เป็นชิ้นงาน</div>
      <div className="space-y-3">
        <Field label="พาธไฟล์ต้นทาง" hint="พาธบนเครื่องที่ backend เข้าถึงได้">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} placeholder="เช่น /Users/.../report.pdf" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="แผนกปลายทาง">
            <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={targetDept} onChange={(e) => setTargetDept(e.target.value)}>
              {depts.length === 0 && <option value="">— ไม่มีแผนก —</option>}
              {depts.map((d) => <option key={d.id} value={d.id}>{d.emoji} {d.name}</option>)}
            </select>
          </Field>
          <Field label="ชื่อชิ้นงาน (ไม่บังคับ)">
            <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={artifactName} onChange={(e) => setArtifactName(e.target.value)} placeholder="ใช้ชื่อไฟล์ถ้าเว้นว่าง" />
          </Field>
        </div>
        <Field label="แท็ก (คั่นด้วยจุลภาค)">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={tags} onChange={(e) => setTags(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-[11px] text-[var(--color-cream-dim)]">
          <Toggle on={copyToWorkspace} onChange={setCopy} /> คัดลอกเข้า workspace ของแผนก
        </label>
        {err && <div className="text-[11px]" style={{ color: ACCENT_HEX.coral }}>{err}</div>}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <GhostBtn onClick={onDone}>ยกเลิก</GhostBtn>
        <PrimaryBtn onClick={submit} disabled={!sourcePath.trim() || !targetDept || busy}>{busy ? 'กำลังนำเข้า…' : 'นำเข้า'}</PrimaryBtn>
      </div>
    </div>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const depts = useSelector((s) => s.departments.filter((d) => !isExec(d.id)).map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const [name, setName] = useState('')
  const [kind, setKind] = useState<ArtifactKind>('doc')
  const [ownerDept, setOwnerDept] = useState(depts[0]?.id ?? '')
  const [uri, setUri] = useState('')
  const [tags, setTags] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = () => {
    if (!name.trim() || !ownerDept || busy) return
    setBusy(true)
    void client
      .createArtifact({
        name: name.trim(),
        kind,
        ownerDept,
        uri: uri.trim() || null,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
      .then(onDone)
      .catch(() => setBusy(false))
  }

  return (
    <div className="mb-3 rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
      <div className="space-y-3">
        <Field label="ชื่อชิ้นงาน">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="เช่น แผนการตลาด Q3" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="ประเภท">
            <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={kind} onChange={(e) => setKind(e.target.value as ArtifactKind)}>
              {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
          </Field>
          <Field label="แผนกเจ้าของ">
            <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={ownerDept} onChange={(e) => setOwnerDept(e.target.value)}>
              {depts.length === 0 && <option value="">— ไม่มีแผนก —</option>}
              {depts.map((d) => <option key={d.id} value={d.id}>{d.emoji} {d.name}</option>)}
            </select>
          </Field>
        </div>
        <Field label="URI / ลิงก์ (ไม่บังคับ)">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={uri} onChange={(e) => setUri(e.target.value)} placeholder="เช่น workspace://docs/plan.md" />
        </Field>
        <Field label="แท็ก (คั่นด้วยจุลภาค)">
          <input className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="เช่น การตลาด, แผน" />
        </Field>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <GhostBtn onClick={onDone}>ยกเลิก</GhostBtn>
        <PrimaryBtn onClick={submit} disabled={!name.trim() || !ownerDept || busy}>สร้างชิ้นงาน</PrimaryBtn>
      </div>
    </div>
  )
}
