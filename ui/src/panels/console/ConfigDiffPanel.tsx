import { useState } from 'react'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { Section, Loading, Empty, ErrorNote, Row, useAsync, useDepts, useNow } from './shared'
import { client } from '../../state/useCompany'

/* Company config diff: every runtime / config / org change is captured as a
 * checkpoint with actor, reason, before/after, result, and a rollback plan.
 * We merge the three durable checkpoint kinds into one reverse-chronological
 * change timeline and expose rollback for reversible org changes. */

type ChkKind = 'org' | 'runtime' | 'tool'
type Chk = Record<string, unknown> & { id: string; ts: number; _kind: ChkKind }

const KIND_META: Record<ChkKind, { label: string; hex: string; entity: string }> = {
  org: { label: 'โครงสร้างองค์กร', hex: ACCENT_HEX.lavender, entity: 'org_checkpoint' },
  runtime: { label: 'รันไทม์เอเจนต์', hex: ACCENT_HEX.sky, entity: 'runtime_checkpoint' },
  tool: { label: 'เครื่องมือ/ไฟล์', hex: ACCENT_HEX.amber, entity: 'checkpoint' },
}

const FILTERS: { id: ChkKind | ''; label: string }[] = [
  { id: '', label: 'ทั้งหมด' },
  { id: 'org', label: 'องค์กร' },
  { id: 'runtime', label: 'รันไทม์' },
  { id: 'tool', label: 'เครื่องมือ' },
]

function statusMeta(status: string): { label: string; hex: string } {
  switch (status) {
    case 'rolled_back':
      return { label: 'ย้อนกลับแล้ว', hex: ACCENT_HEX.honey }
    case 'error':
      return { label: 'ผิดพลาด', hex: ACCENT_HEX.coral }
    case 'applied':
      return { label: 'มีผลแล้ว', hex: ACCENT_HEX.teal }
    case 'created':
      return { label: 'บันทึกแล้ว', hex: ACCENT_HEX.teal }
    default:
      return { label: status || 'active', hex: ACCENT_HEX.teal }
  }
}

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v))
const arr = (v: unknown): string[] => (Array.isArray(v) ? v.map(str) : [])

export function ConfigDiffPanel() {
  const [filter, setFilter] = useState<ChkKind | ''>('')
  const { data, loading, error, reload } = useAsync(async () => {
    const [org, runtime, tool] = await Promise.all([
      client.listEntities<Record<string, unknown>>('org_checkpoint', { limit: 120 }).catch(() => []),
      client.listEntities<Record<string, unknown>>('runtime_checkpoint', { limit: 80 }).catch(() => []),
      client.listEntities<Record<string, unknown>>('checkpoint', { limit: 80 }).catch(() => []),
    ])
    const tag = (rows: Record<string, unknown>[], kind: ChkKind): Chk[] =>
      rows
        .filter((r) => typeof r.id === 'string')
        .map((r) => ({ ...r, id: r.id as string, ts: Number(r.ts) || 0, _kind: kind }))
    return [...tag(org, 'org'), ...tag(runtime, 'runtime'), ...tag(tool, 'tool')].sort(
      (a, b) => b.ts - a.ts,
    )
  }, [])

  const all = data ?? []
  const rows = filter ? all.filter((c) => c._kind === filter) : all

  return (
    <Section
      title="ไดอารีการเปลี่ยนค่าระบบ (Config Diff)"
      hint="ทุกการเปลี่ยนองค์กร · รันไทม์ · เครื่องมือ — ใคร เพราะอะไร ก่อน/หลัง และแผนย้อนกลับ"
      actions={
        <div className="flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}>
          {FILTERS.map((f) => {
            const on = filter === f.id
            return (
              <button
                key={f.id || 'all'}
                type="button"
                onClick={() => setFilter(f.id)}
                className="rounded-md px-2 py-1 text-[11px] font-medium transition-colors"
                style={{ background: on ? ACCENT_HEX.amber : 'transparent', color: on ? '#1a1610' : 'var(--color-cream-dim)' }}
              >
                {f.label}
              </button>
            )
          })}
        </div>
      }
    >
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : rows.length === 0 && !error ? (
        <Empty text="ยังไม่มีการเปลี่ยนแปลงที่ถูกบันทึกเป็นเช็กพอยต์" />
      ) : (
        <div className="space-y-2.5">
          {rows.map((c) => (
            <ChangeRow key={c.id} c={c} onChanged={reload} />
          ))}
        </div>
      )}
    </Section>
  )
}

function ChangeRow({ c, onChanged }: { c: Chk; onChanged: () => void }) {
  const now = useNow()
  const { label } = useDepts()
  const [open, setOpen] = useState(false)
  const km = KIND_META[c._kind]
  const sm = statusMeta(str(c.status) || (c._kind === 'tool' ? 'created' : 'active'))

  const title =
    str(c.action) || str(c.tool) || str(c.reason) || (c._kind === 'runtime' ? 'runtime checkpoint' : 'checkpoint')
  const actor = str(c.actor) || (c._kind === 'tool' ? str(c.requestedBy) : '')
  const deptId = str(c.departmentId)
  const reason = str(c.reason)

  return (
    <Row accent={km.hex}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="block w-full text-left">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
            {title}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Pill color={km.hex}>{km.label}</Pill>
            <Pill color={sm.hex}>{sm.label}</Pill>
          </div>
        </div>
        {reason && reason !== title && (
          <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
            {reason}
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[var(--color-cream-faint)]">
          {actor && <span>โดย {label(actor)}</span>}
          {deptId && <span>· {label(deptId)}</span>}
          <span className="font-mono" style={{ fontFamily: 'var(--font-mono)' }}>· {c.id}</span>
          <span className="ml-auto">{relTime(c.ts, now)}ที่แล้ว</span>
        </div>
      </button>
      {open && <ChangeDetail c={c} onChanged={onChanged} />}
    </Row>
  )
}

function ChangeDetail({ c, onChanged }: { c: Chk; onChanged: () => void }) {
  const { label } = useDepts()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const before = arr(c.beforeDepartmentIds)
  const after = arr(c.afterDepartmentIds)
  const removed = before.filter((id) => !after.includes(id))
  const added = after.filter((id) => !before.includes(id))
  const snapshots = Array.isArray(c.snapshots) ? (c.snapshots as Record<string, unknown>[]) : []
  const plan = (c.rollbackPlan && typeof c.rollbackPlan === 'object' ? c.rollbackPlan : null) as
    | Record<string, unknown>
    | null
  const canRollback = c._kind === 'org' && ['active', 'applied'].includes(str(c.status))

  const doRollback = () => {
    if (busy) return
    if (!confirm('ย้อนการเปลี่ยนแปลงนี้กลับไปยังสแนปช็อตก่อนหน้า?')) return
    setBusy(true)
    setResult(null)
    void client
      .rollbackOrgCheckpoint(c.id)
      .then((r) => {
        const restored = arr(r.restored).length
        const removedN = arr(r.removed).length
        setResult(`ย้อนกลับสำเร็จ · กู้คืน ${restored} แผนก · ลบ ${removedN} แผนก`)
        onChanged()
      })
      .catch((e) => setResult(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }

  return (
    <div className="mt-3 space-y-2.5 border-t pt-3 text-[11px]" style={{ borderColor: 'var(--color-line-soft)' }}>
      {/* org topology before / after */}
      {(added.length > 0 || removed.length > 0) && (
        <div className="grid grid-cols-2 gap-2">
          <DiffCol title="ก่อน (ถูกลบ)" hex={ACCENT_HEX.coral} items={removed.map(label)} empty="— ไม่มี" />
          <DiffCol title="หลัง (เพิ่มใหม่)" hex={ACCENT_HEX.teal} items={added.map(label)} empty="— ไม่มี" />
        </div>
      )}
      {before.length > 0 && added.length === 0 && removed.length === 0 && (
        <Meta label="โครงสร้างไม่เปลี่ยน" value={`${before.length} แผนก`} />
      )}

      {/* file snapshots (tool checkpoints) */}
      {snapshots.map((s, i) => (
        <FileSnapshot key={i} s={s} />
      ))}

      {/* runtime checkpoint specifics */}
      {c._kind === 'runtime' && (
        <div className="space-y-1">
          {str(c.backend) && <Meta label="แบ็กเอนด์" value={str(c.backend)} mono />}
          {str(c.agentKey) && <Meta label="เอเจนต์" value={str(c.agentKey)} mono />}
          <Meta label="มีสแนปช็อต" value={c.snapshotAvailable ? 'ใช่' : 'ไม่'} />
        </div>
      )}

      {/* rollback plan */}
      {plan && (
        <div className="rounded-lg border p-2" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}>
          <div className="mb-1 text-[10px] font-semibold tracking-wide text-[var(--color-cream-faint)] uppercase">แผนย้อนกลับ</div>
          {str(plan.mode) && <Meta label="โหมด" value={str(plan.mode)} mono />}
          {str(plan.note) && <div className="text-[11px] text-[var(--color-cream-dim)]">{str(plan.note)}</div>}
          {arr(plan.restores).length > 0 && <Meta label="กู้คืน" value={arr(plan.restores).map(label).join(', ')} />}
          {arr(plan.removes).length > 0 && <Meta label="ลบ" value={arr(plan.removes).map(label).join(', ')} />}
          {str(plan.endpoint) && <Meta label="endpoint" value={str(plan.endpoint)} mono />}
        </div>
      )}

      {/* rollback action */}
      {canRollback && (
        <div className="flex items-center justify-between gap-2 pt-0.5">
          <span className="text-[10px] text-[var(--color-cream-faint)]">การเปลี่ยนนี้ย้อนกลับได้ (Full Auto · มีบันทึกตรวจสอบ)</span>
          <button
            type="button"
            onClick={doRollback}
            disabled={busy}
            className="rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-opacity disabled:opacity-40"
            style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.45), color: ACCENT_HEX.coral, background: withAlpha(ACCENT_HEX.coral, 0.08) }}
          >
            {busy ? 'กำลังย้อนกลับ…' : 'ย้อนการเปลี่ยนแปลง'}
          </button>
        </div>
      )}
      {result && (
        <div className="rounded-lg px-2 py-1.5 text-[11px]" style={{ background: 'var(--color-surface)', color: 'var(--color-cream-dim)' }}>
          {result}
        </div>
      )}
    </div>
  )
}

function DiffCol({ title, hex, items, empty }: { title: string; hex: string; items: string[]; empty: string }) {
  return (
    <div className="rounded-lg border p-2" style={{ borderColor: withAlpha(hex, 0.3), background: withAlpha(hex, 0.05) }}>
      <div className="mb-1 text-[10px] font-semibold" style={{ color: hex }}>{title}</div>
      {items.length === 0 ? (
        <div className="text-[10px] text-[var(--color-cream-faint)]">{empty}</div>
      ) : (
        <div className="space-y-0.5">
          {items.map((it, i) => (
            <div key={i} className="text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{it}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function FileSnapshot({ s }: { s: Record<string, unknown> }) {
  const path = str(s.path)
  const before = str(s.before)
  const after = str(s.after)
  const clip = (t: string) => (t.length > 600 ? t.slice(0, 600) + '…' : t)
  return (
    <div className="rounded-lg border p-2" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}>
      <div className="mb-1 truncate font-mono text-[10px] text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>{path || 'snapshot'}</div>
      <div className="grid grid-cols-2 gap-2">
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded p-1.5 text-[10px]" style={{ background: withAlpha(ACCENT_HEX.coral, 0.06), fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>
          {before ? clip(before) : '— ไม่มีไฟล์เดิม'}
        </pre>
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded p-1.5 text-[10px]" style={{ background: withAlpha(ACCENT_HEX.teal, 0.06), fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>
          {after ? clip(after) : '— ไม่มีไฟล์ใหม่'}
        </pre>
      </div>
    </div>
  )
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-[11px]">
      <span className="shrink-0 text-[var(--color-cream-faint)]">{label}:</span>
      <span
        className="min-w-0 break-words text-[var(--color-cream-dim)] [overflow-wrap:anywhere]"
        style={mono ? { fontFamily: 'var(--font-mono)' } : undefined}
      >
        {value}
      </span>
    </div>
  )
}
