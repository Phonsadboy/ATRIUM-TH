import { useState } from 'react'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { EXEC_ID } from '../../lib/threads'
import { client } from '../../state/useCompany'
import type { DepartmentMemory, Preference } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, useAsync, useDepts, useNow } from './shared'

/* Unified memory inspector — the five layered memories from the v2 design, each
 * surfacing source, confidence, valid-time, and a link back to the raw ledger.
 *   owner · company · project · department · procedural                       */

type Layer = 'owner' | 'company' | 'department' | 'project' | 'procedural'
const LAYERS: { id: Layer; label: string; hex: string }[] = [
  { id: 'owner', label: 'เจ้าของ', hex: ACCENT_HEX.honey },
  { id: 'company', label: 'บริษัท', hex: ACCENT_HEX.amber },
  { id: 'department', label: 'แผนก', hex: ACCENT_HEX.teal },
  { id: 'project', label: 'โปรเจกต์', hex: ACCENT_HEX.sky },
  { id: 'procedural', label: 'ทักษะ/บทเรียน', hex: ACCENT_HEX.lavender },
]

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v))
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function MemoryPanel() {
  const [layer, setLayer] = useState<Layer>('owner')
  return (
    <Section
      title="คลังความจำ (Memory Inspector)"
      hint="ความจำทุกชั้น พร้อมแหล่งที่มา · ความเชื่อมั่น · ช่วงเวลาที่ใช้ได้ · ลิงก์ต้นฉบับ"
    >
      <div
        className="mb-3 flex flex-wrap gap-1 rounded-xl p-1"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
      >
        {LAYERS.map((l) => {
          const on = layer === l.id
          return (
            <button
              key={l.id}
              type="button"
              onClick={() => setLayer(l.id)}
              className="flex-1 rounded-lg px-2 py-1.5 text-[11px] font-medium whitespace-nowrap transition-colors"
              style={{ background: on ? l.hex : 'transparent', color: on ? '#1a1610' : 'var(--color-cream-dim)' }}
            >
              {l.label}
            </button>
          )
        })}
      </div>
      {layer === 'owner' && <OwnerMemory />}
      {layer === 'company' && <DeptMemory fixedId={EXEC_ID} />}
      {layer === 'department' && <DeptMemory />}
      {layer === 'project' && <ProjectMemory />}
      {layer === 'procedural' && <ProceduralMemory />}
    </Section>
  )
}

/* ---------- transcript backlink (raw ledger lookup) ---------- */

function TranscriptLink({ query, deptId }: { query: string; deptId?: string }) {
  const now = useNow()
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = () => {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    if (rows || busy) return
    setBusy(true)
    void client
      .lookupLedger({ q: query.slice(0, 120), department_id: deptId, semantic: true, limit: 5 })
      .then((r) =>
        setRows(
          Array.isArray(r) ? (r as Record<string, unknown>[]) : ((r.rows as Record<string, unknown>[]) ?? []),
        ),
      )
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }

  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={load}
        className="text-[10px] font-medium transition-colors hover:text-[var(--color-cream)]"
        style={{ color: ACCENT_HEX.sky }}
      >
        {open ? 'ซ่อนต้นฉบับ' : '↳ ดูต้นฉบับใน ledger'}
      </button>
      {open && (
        <div className="mt-1 space-y-1">
          {busy && <div className="text-[10px] text-[var(--color-cream-faint)]">กำลังค้นต้นฉบับ…</div>}
          {err && <div className="text-[10px]" style={{ color: ACCENT_HEX.coral }}>{err}</div>}
          {rows && rows.length === 0 && (
            <div className="text-[10px] text-[var(--color-cream-faint)]">ไม่พบรายการต้นฉบับที่ตรงกัน</div>
          )}
          {rows?.map((r, i) => (
            <div key={i} className="rounded-md px-2 py-1 text-[10px]" style={{ background: 'var(--color-surface)' }}>
              <span className="font-mono text-[var(--color-cream-faint)]" style={{ fontFamily: 'var(--font-mono)' }}>
                {str(r.eventType) || 'event'}
              </span>
              <span className="ml-1 text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                {str(r.summary) || str(r.id)}
              </span>
              {num(r.ts) != null && (
                <span className="ml-1 text-[var(--color-cream-faint)]">· {relTime(num(r.ts) as number, now)}ที่แล้ว</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const hex = pct >= 70 ? ACCENT_HEX.teal : pct >= 40 ? ACCENT_HEX.honey : ACCENT_HEX.coral
  return (
    <span
      className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
      style={{ fontFamily: 'var(--font-mono)', color: hex, background: withAlpha(hex, 0.12) }}
      title="ความเชื่อมั่น"
    >
      {pct}%
    </span>
  )
}

function Note({ text }: { text: string }) {
  return (
    <div
      className="rounded-xl border px-3 py-2 text-[11px] leading-relaxed text-[var(--color-cream-dim)]"
      style={{ borderColor: 'var(--color-line-soft)', background: withAlpha(ACCENT_HEX.amber, 0.05) }}
    >
      {text}
    </div>
  )
}

/* ---------- owner ---------- */

function OwnerMemory() {
  const { data, loading, error, reload } = useAsync(() => client.getOwnerProfile(), [])
  const prefs: Preference[] = data?.preferences ?? []
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (prefs.length === 0) return <Empty text="ยังไม่มีโปรไฟล์/ความชอบของเจ้าของ" />
  return (
    <div className="space-y-2">
      <Note text="สิ่งที่บริษัทจดจำเกี่ยวกับคุณ — สไตล์การสื่อสาร โค้ด ดีไซน์ ความเสี่ยง และกฎประจำ (USER.md)" />
      {prefs.map((p) => (
        <Row key={p.id} accent={ACCENT_HEX.honey}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-[12px] text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{p.text}</div>
            <div className="flex shrink-0 items-center gap-1">
              <Confidence value={p.confidence} />
              <Pill color={ACCENT_HEX.honey}>{p.category}</Pill>
            </div>
          </div>
          {p.source && (
            <div className="mt-1 text-[10px] text-[var(--color-cream-faint)]">แหล่งที่มา: {p.source}</div>
          )}
        </Row>
      ))}
    </div>
  )
}

/* ---------- department / company (department memory) ---------- */

interface GraphFact {
  from: string
  to: string
  rel: string
  validFrom?: number | null
  validTo?: number | null
  confidence?: number | null
  source?: string | null
}

function DeptMemory({ fixedId }: { fixedId?: string }) {
  const { list } = useDepts()
  const fallback = list.find((d) => !d.isExec)?.id ?? list[0]?.id ?? ''
  const [picked, setPicked] = useState<string>('')
  const deptId = fixedId ?? (picked || fallback)
  const { data, loading, error, reload } = useAsync<DepartmentMemory>(
    () => client.getDepartmentMemory(deptId),
    [deptId],
  )
  const now = useNow()

  const knowledge = data?.knowledge ?? []
  const nodes = (data?.graph?.nodes ?? []) as { id: string; label: string }[]
  const edges = (data?.graph?.edges ?? []) as unknown as GraphFact[]
  const nodeLabel = (id: string) => nodes.find((n) => n.id === id)?.label ?? id

  return (
    <div className="space-y-3">
      {fixedId ? (
        <Note text="ความจำระดับบริษัทผ่านผู้บริหาร — การตัดสินใจ กฎการดำเนินงาน และบทเรียนสะสม (MEMORY.md)" />
      ) : (
        <select
          value={deptId}
          onChange={(e) => setPicked(e.target.value)}
          className="w-full rounded-lg border bg-[var(--color-surface)] px-2 py-1.5 text-[12px] text-[var(--color-cream)] outline-none"
          style={{ borderColor: 'var(--color-line-soft)' }}
        >
          {list.map((d) => (
            <option key={d.id} value={d.id}>
              {d.emoji} {d.name}
            </option>
          ))}
        </select>
      )}

      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : (
        <>
          <div>
            <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">
              ความรู้ (RAG · {knowledge.length})
            </div>
            {knowledge.length === 0 ? (
              <Empty text="ยังไม่มีความรู้ที่สกัดไว้" />
            ) : (
              <div className="space-y-2">
                {knowledge
                  .slice()
                  .sort((a, b) => b.score - a.score)
                  .map((k) => (
                    <Row key={k.id} accent={ACCENT_HEX.teal}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                          {k.title}
                        </div>
                        <Confidence value={k.score} />
                      </div>
                      <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                        {k.text}
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-cream-faint)]">
                        {k.tags?.map((t) => (
                          <span key={t} className="rounded px-1 py-0.5" style={{ background: 'var(--color-surface-3)' }}>
                            #{t}
                          </span>
                        ))}
                        {k.source && <span>· แหล่งที่มา {k.source}</span>}
                        <span className="ml-auto">{relTime(k.ts, now)}ที่แล้ว</span>
                      </div>
                      <TranscriptLink query={k.title || k.text} deptId={deptId} />
                    </Row>
                  ))}
              </div>
            )}
          </div>

          <div>
            <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">
              ข้อเท็จจริงเชิงกราฟ (เวลาใช้ได้ · {edges.length})
            </div>
            {edges.length === 0 ? (
              <Empty text="ยังไม่มีข้อเท็จจริงเชิงกราฟ" />
            ) : (
              <div className="space-y-1.5">
                {edges.map((e, i) => (
                  <div
                    key={i}
                    className="rounded-xl border p-2.5"
                    style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
                  >
                    <div className="text-[11px] text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                      <span className="font-medium">{nodeLabel(e.from)}</span>
                      <span className="mx-1 text-[var(--color-cream-faint)]">—{e.rel}→</span>
                      <span className="font-medium">{nodeLabel(e.to)}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-[var(--color-cream-faint)]">
                      <ValidWindow from={e.validFrom} to={e.validTo} now={now} />
                      {num(e.confidence) != null && (
                        <span>· เชื่อมั่น {Math.round((num(e.confidence) as number) * 100)}%</span>
                      )}
                      {e.source && <span>· {e.source}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function ValidWindow({ from, to, now }: { from?: number | null; to?: number | null; now: number }) {
  const f = num(from)
  const t = num(to)
  if (f == null && t == null) return <span>ใช้ได้: ตลอด</span>
  return (
    <span>
      ใช้ได้: {f != null ? relTime(f, now) + 'ที่แล้ว' : 'เริ่มต้น'} → {t != null ? relTime(t, now) + 'ที่แล้ว' : 'ปัจจุบัน'}
    </span>
  )
}

/* ---------- project ---------- */

function ProjectMemory() {
  const projectsState = useAsync(() => client.listProjects({ limit: 100 }), [])
  const [picked, setPicked] = useState<string>('')
  const list = projectsState.data ?? []
  const projectId = picked || list[0]?.id || ''

  const memState = useAsync(async () => {
    if (!projectId) return { decisions: [], artifacts: [] }
    const [decisions, artifacts] = await Promise.all([
      client.listDecisions({ project: projectId, limit: 50 }).catch(() => []),
      client.listArtifacts({ project: projectId, limit: 50 }).catch(() => []),
    ])
    return { decisions, artifacts }
  }, [projectId])

  if (projectsState.loading && !projectsState.data) return <Loading />
  if (projectsState.error) return <ErrorNote error={projectsState.error} onRetry={projectsState.reload} />
  if (list.length === 0) return <Empty text="ยังไม่มีโปรเจกต์" />

  const decisions = memState.data?.decisions ?? []
  const artifacts = memState.data?.artifacts ?? []

  return (
    <div className="space-y-3">
      <select
        value={projectId}
        onChange={(e) => setPicked(e.target.value)}
        className="w-full rounded-lg border bg-[var(--color-surface)] px-2 py-1.5 text-[12px] text-[var(--color-cream)] outline-none"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        {list.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      {memState.error && <ErrorNote error={memState.error} onRetry={memState.reload} />}
      {memState.loading && !memState.data ? (
        <Loading />
      ) : (
        <>
          <div>
            <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">
              การตัดสินใจ ({decisions.length})
            </div>
            {decisions.length === 0 ? (
              <Empty text="ยังไม่มีการตัดสินใจในโปรเจกต์นี้" />
            ) : (
              <div className="space-y-1.5">
                {decisions.map((d) => (
                  <Row key={d.id} accent={ACCENT_HEX.sky}>
                    <div className="text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                      {d.title}
                    </div>
                    {d.rationale && (
                      <div className="mt-0.5 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                        {d.rationale}
                      </div>
                    )}
                    <TranscriptLink query={d.title} />
                  </Row>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">
              ชิ้นงาน ({artifacts.length})
            </div>
            {artifacts.length === 0 ? (
              <Empty text="ยังไม่มีชิ้นงานในโปรเจกต์นี้" />
            ) : (
              <div className="space-y-1.5">
                {artifacts.map((a) => (
                  <Row key={a.id} accent={ACCENT_HEX.lavender}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                        {a.name}
                      </div>
                      <Pill color={ACCENT_HEX.lavender}>{a.kind}</Pill>
                    </div>
                  </Row>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/* ---------- procedural ---------- */

interface LessonRow {
  id: string
  lesson?: string
  whatWentWrong?: string
  source?: string
  ts?: number
  departmentId?: string
}

function ProceduralMemory() {
  const now = useNow()
  const { label } = useDepts()
  const { data, loading, error, reload } = useAsync(async () => {
    const [lessons, playbooks, skills] = await Promise.all([
      client.listEntities<LessonRow>('lesson', { limit: 60 }).catch(() => []),
      client.listPlaybooks({ limit: 60 }).catch(() => []),
      client.listSkills({ limit: 60 }).catch(() => []),
    ])
    return { lessons, playbooks, skills }
  }, [])

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  const lessons = data?.lessons ?? []
  const playbooks = data?.playbooks ?? []
  const skills = data?.skills ?? []
  if (lessons.length + playbooks.length + skills.length === 0)
    return <Empty text="ยังไม่มีทักษะ คู่มือ หรือบทเรียน" />

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">บทเรียน ({lessons.length})</div>
        <div className="space-y-1.5">
          {lessons.map((l) => (
            <Row key={l.id} accent={ACCENT_HEX.lavender}>
              <div className="text-[12px] text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                {l.lesson || l.whatWentWrong || l.id}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-[var(--color-cream-faint)]">
                {l.source && <span>จาก {l.source}</span>}
                {l.departmentId && <span>· {label(l.departmentId)}</span>}
                {num(l.ts) != null && <span className="ml-auto">{relTime(num(l.ts) as number, now)}ที่แล้ว</span>}
              </div>
            </Row>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">
          คู่มือปฏิบัติ ({playbooks.length})
        </div>
        <div className="space-y-1.5">
          {playbooks.map((p) => (
            <Row key={p.id} accent={ACCENT_HEX.teal}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                  {p.name}
                </div>
                {typeof p.version === 'number' && <Pill color={ACCENT_HEX.teal}>v{p.version}</Pill>}
              </div>
              {p.whenToUse && (
                <div className="mt-0.5 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                  {p.whenToUse}
                </div>
              )}
            </Row>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1.5 text-[11px] font-semibold text-[var(--color-cream-dim)]">ทักษะ ({skills.length})</div>
        <div className="space-y-1.5">
          {skills.map((s) => (
            <Row key={s.id} accent={ACCENT_HEX.amber}>
              <div className="text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
                {s.name}
              </div>
              {s.description && (
                <div className="mt-0.5 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
                  {s.description}
                </div>
              )}
            </Row>
          ))}
        </div>
      </div>
    </div>
  )
}
