import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { useSelector, client } from '../state/useCompany'
import { cx, Pill, inputClass, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { compactNum, relTime } from '../lib/format'
import type {
  AccentName,
  GraphNodeType,
  MemoryArchiveEntry,
  MemoryGraph,
  MemoryKind,
  MemoryKnowledgeEntry,
} from '../contract/types'

const KIND_LABEL: Record<MemoryKind, string> = {
  knowledge: 'ความรู้ (RAG)',
  archive: 'คลังบีบอัด',
  graph: 'กราฟความรู้',
}

const NODE_HEX: Record<GraphNodeType, string> = {
  concept: '#f4a945',
  entity: '#7eb0dc',
  task: '#57d6bf',
  person: '#b3a4ee',
  artifact: '#e8c07a',
}

const NODE_LABEL: Record<GraphNodeType, string> = {
  concept: 'แนวคิด',
  entity: 'สิ่งของ',
  task: 'งาน',
  person: 'บุคคล',
  artifact: 'ชิ้นงาน',
}

export function MemoryViewer({ deptId, accent }: { deptId: string; accent: AccentName }) {
  const stats = useSelector((s) => s.departments.find((d) => d.id === deptId)?.memory ?? null)
  const now = useSelector((s) => s.now)
  const [kind, setKind] = useState<MemoryKind>('knowledge')
  // bumped after an in-place edit (which leaves the memory stats untouched) so
  // the memo below re-fetches the freshly-mutated knowledge entry.
  const [memRev, setMemRev] = useState(0)

  // getMemory is a plain external getter, so these deps are intentional triggers,
  // not values read in the body: re-fetch when compaction OR a manual edit/delete
  // mutates the dept's memory. ragEntries covers compaction growth + deletions
  // (previously omitted, so the knowledge list went stale as it grew); memRev
  // catches in-place edits, which leave the memory stats untouched.
  /* eslint-disable react-hooks/exhaustive-deps */
  const memory = useMemo(
    () => client.getMemory(deptId),
    [deptId, stats?.archiveChunks, stats?.ragEntries, stats?.lastCompactionAt, stats?.graphNodes, memRev],
  )
  /* eslint-enable react-hooks/exhaustive-deps */

  const accentHex = ACCENT_HEX[accent]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* stats */}
      {stats && (
        <div className="mb-3 grid grid-cols-3 gap-1.5">
          <Stat label="คลังดิบ" value={compactNum(stats.archiveChunks)} accent={accentHex} />
          <Stat label="ความรู้" value={compactNum(stats.ragEntries)} accent={accentHex} />
          <Stat
            label="โหนด·เส้น"
            value={`${stats.graphNodes}·${stats.graphEdges}`}
            accent={accentHex}
          />
          <div className="col-span-3 flex items-center justify-between rounded-xl border px-3 py-2"
            style={{ borderColor: 'var(--color-line-soft)', background: withAlpha(accentHex, 0.06) }}>
            <span className="text-[11px] text-[var(--color-cream-dim)]">
              บีบอัดล่าสุด{' '}
              {stats.lastCompactionAt ? relTime(stats.lastCompactionAt, now) + 'ที่แล้ว' : '—'}
            </span>
            <span
              className="text-[11px] font-medium tabular-nums"
              style={{ fontFamily: 'var(--font-mono)', color: accentHex }}
            >
              ประหยัด {compactNum(stats.tokensSaved)} โทเค็น
            </span>
          </div>
        </div>
      )}

      {/* inner tab switcher */}
      <div
        className="mb-3 flex gap-1 rounded-xl p-1"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
      >
        {(['knowledge', 'archive', 'graph'] as MemoryKind[]).map((k) => {
          const on = kind === k
          return (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className="relative flex-1 rounded-lg px-2 py-1.5 text-[11px] font-medium transition-colors"
              style={{ color: on ? '#1a1610' : 'var(--color-cream-dim)' }}
            >
              {on && (
                <motion.span
                  layoutId={`mem-tab-${deptId}`}
                  className="absolute inset-0 rounded-lg"
                  style={{ background: accentHex }}
                  transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                />
              )}
              <span className="relative z-10">{KIND_LABEL[k]}</span>
            </button>
          )
        })}
      </div>

      {/* body */}
      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto pr-1">
        {kind === 'knowledge' && (
          <KnowledgeList
            items={memory.knowledge}
            accent={accentHex}
            now={now}
            deptId={deptId}
            onEdited={() => setMemRev((r) => r + 1)}
          />
        )}
        {kind === 'archive' && <ArchiveList items={memory.archive} accent={accentHex} now={now} />}
        {kind === 'graph' && <GraphView graph={memory.graph} />}
      </div>
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <div
      className="rounded-xl border px-2.5 py-2"
      style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
    >
      <div
        className="text-[15px] leading-none font-semibold tabular-nums"
        style={{ fontFamily: 'var(--font-mono)', color: accent }}
      >
        {value}
      </div>
      <div className="mt-1 text-[10px] text-[var(--color-cream-faint)]">{label}</div>
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="mt-10 text-center text-sm text-[var(--color-cream-faint)]">{text}</div>
  )
}

function KnowledgeList({
  items,
  accent,
  now,
  deptId,
  onEdited,
}: {
  items: MemoryKnowledgeEntry[]
  accent: string
  now: number
  deptId: string
  onEdited: () => void
}) {
  if (items.length === 0) return <Empty text="ยังไม่มีความรู้ที่สกัดไว้" />
  const sorted = items.slice().sort((a, b) => b.score - a.score)
  return (
    <div className="space-y-2">
      {sorted.map((k) => (
        <KnowledgeCard
          key={k.id}
          entry={k}
          accent={accent}
          now={now}
          deptId={deptId}
          onEdited={onEdited}
        />
      ))}
    </div>
  )
}

function KnowledgeCard({
  entry: k,
  accent,
  now,
  deptId,
  onEdited,
}: {
  entry: MemoryKnowledgeEntry
  accent: string
  now: number
  deptId: string
  onEdited: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(k.title)
  const [text, setText] = useState(k.text)

  const beginEdit = () => {
    setTitle(k.title)
    setText(k.text)
    setEditing(true)
  }

  const cancel = () => setEditing(false)

  const save = () => {
    const nextTitle = title.trim()
    const nextText = text.trim()
    if (!nextTitle && !nextText) return
    client.editKnowledge(deptId, k.id, {
      title: nextTitle || k.title,
      text: nextText || k.text,
    })
    setEditing(false)
    onEdited()
  }

  return (
    <div
      className="rounded-2xl border p-3"
      style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
    >
      {editing ? (
        <div className="space-y-2">
          <input
            className={inputClass}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="หัวข้อ"
            autoFocus
          />
          <textarea
            className={cx(inputClass, 'min-h-[72px] resize-y leading-relaxed')}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="เนื้อหา"
          />
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={cancel}
              className="rounded-lg px-2.5 py-1 text-[11px] font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
            >
              ยกเลิก
            </button>
            <button
              type="button"
              onClick={save}
              className="rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-colors"
              style={{ color: '#1a1610', background: accent }}
            >
              บันทึก
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{k.title}</div>
            <div className="flex shrink-0 items-center gap-1">
              <span
                className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
                style={{
                  fontFamily: 'var(--font-mono)',
                  color: accent,
                  background: withAlpha(accent, 0.12),
                }}
                title="คะแนนความเกี่ยวข้อง"
              >
                {Math.round(k.score * 100)}%
              </span>
              <button
                type="button"
                onClick={beginEdit}
                title="แก้ไข"
                aria-label="แก้ไขความรู้"
                className="inline-flex h-5 w-5 items-center justify-center rounded-md text-[11px] text-[var(--color-cream-faint)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-cream)]"
              >
                ✎
              </button>
              <button
                type="button"
                onClick={() => client.deleteKnowledge(deptId, k.id)}
                title="ลบ"
                aria-label="ลบความรู้"
                className="inline-flex h-5 w-5 items-center justify-center rounded-md text-[11px] text-[var(--color-cream-faint)] transition-colors hover:text-[var(--color-coral)]"
              >
                ✕
              </button>
            </div>
          </div>
          <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
            {k.text}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {k.tags.map((t) => (
              <span
                key={t}
                className="rounded-md px-1.5 py-0.5 text-[10px] text-[var(--color-cream-faint)]"
                style={{ background: 'var(--color-surface-3)' }}
              >
                #{t}
              </span>
            ))}
            <span className="ml-auto text-[10px] text-[var(--color-cream-faint)]">
              {relTime(k.ts, now)}ที่แล้ว
            </span>
          </div>
        </>
      )}
    </div>
  )
}

function ArchiveList({
  items,
  accent,
  now,
}: {
  items: MemoryArchiveEntry[]
  accent: string
  now: number
}) {
  if (items.length === 0) return <Empty text="ยังไม่มีการบีบอัดความจำ" />
  return (
    <div className="relative space-y-2 pl-3">
      <div
        className="absolute top-1 bottom-1 left-0 w-px"
        style={{ background: 'var(--color-line-soft)' }}
      />
      {items.map((a) => (
        <div
          key={a.id}
          className="relative rounded-2xl border p-3"
          style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
        >
          <span
            className="absolute top-4 -left-[14px] h-2 w-2 rounded-full"
            style={{ background: accent, boxShadow: `0 0 6px ${accent}` }}
          />
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{a.title}</div>
            <span className="shrink-0 text-[10px] text-[var(--color-cream-faint)]">
              {relTime(a.ts, now)}ที่แล้ว
            </span>
          </div>
          <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">
            {a.summary}
          </div>
          <div className="mt-2">
            <Pill color={accent}>ต้นฉบับ {compactNum(a.tokens)} โทเค็น</Pill>
          </div>
        </div>
      ))}
    </div>
  )
}

/** Rough glyph-width estimate for SVG labels (no DOM measurement available). */
function estTextWidth(s: string, fontSize: number) {
  return s.length * fontSize * 0.62
}

function truncate(s: string, max: number) {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

function GraphView({ graph }: { graph: MemoryGraph }) {
  // Larger internal coordinate space than before (was 320×230): the SVG still
  // scales to the container via w-full, but more units per node means labels
  // have room to breathe and overlap far less on the narrow dock.
  const W = 440
  const H = 300
  const pad = 34
  const R = 6.5
  const NODE_FS = 9
  const EDGE_FS = 7.5
  const NODE_MAX = 16
  const EDGE_MAX = 14
  const pos = (n: { x: number; y: number }) => ({
    cx: pad + n.x * (W - 2 * pad),
    cy: pad + n.y * (H - 2 * pad),
  })
  const byId = useMemo(() => {
    const m = new Map(graph.nodes.map((n) => [n.id, n]))
    return m
  }, [graph])

  if (graph.nodes.length === 0) return <Empty text="ยังไม่มีกราฟความรู้" />

  const usedTypes = Array.from(new Set(graph.nodes.map((n) => n.type)))

  return (
    <div className="space-y-2">
      <div
        className="overflow-hidden rounded-2xl border"
        style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          style={{ display: 'block' }}
          role="img"
          aria-label="กราฟความรู้"
        >
          {/* edges + relation labels first, so node labels paint on top */}
          {graph.edges.map((e, i) => {
            const a = byId.get(e.from)
            const b = byId.get(e.to)
            if (!a || !b) return null
            const pa = pos(a)
            const pb = pos(b)
            const mx = (pa.cx + pb.cx) / 2
            const my = (pa.cy + pb.cy) / 2
            const label = truncate(e.rel, EDGE_MAX)
            const tw = estTextWidth(label, EDGE_FS)
            return (
              <g key={i}>
                <line
                  x1={pa.cx}
                  y1={pa.cy}
                  x2={pb.cx}
                  y2={pb.cy}
                  stroke="var(--color-line)"
                  strokeWidth={1}
                />
                {/* solid pill behind the relation so crossing lines/labels don't muddy it */}
                <rect
                  x={mx - tw / 2 - 3}
                  y={my - EDGE_FS / 2 - 2.5}
                  width={tw + 6}
                  height={EDGE_FS + 5}
                  rx={(EDGE_FS + 5) / 2}
                  fill="var(--color-surface)"
                  stroke="var(--color-line-soft)"
                  strokeWidth={0.75}
                />
                <text
                  x={mx}
                  y={my}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={EDGE_FS}
                  fill="var(--color-cream-faint)"
                >
                  <title>{e.rel}</title>
                  {label}
                </text>
              </g>
            )
          })}
          {graph.nodes.map((n) => {
            const p = pos(n)
            const hex = NODE_HEX[n.type]
            const label = truncate(n.label, NODE_MAX)
            const tw = estTextWidth(label, NODE_FS)
            const ly = p.cy + R + 4
            return (
              <g key={n.id}>
                <circle
                  cx={p.cx}
                  cy={p.cy}
                  r={R}
                  fill={hex}
                  stroke="var(--color-surface)"
                  strokeWidth={1.5}
                />
                {/* subtle backing plate keeps the label legible over edges/labels */}
                <rect
                  x={p.cx - tw / 2 - 3}
                  y={ly - 1}
                  width={tw + 6}
                  height={NODE_FS + 3}
                  rx={4}
                  fill="var(--color-surface-2)"
                  opacity={0.85}
                />
                <text
                  x={p.cx}
                  y={ly + NODE_FS / 2 + 0.5}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={NODE_FS}
                  fill="var(--color-cream)"
                  style={{ fontWeight: 500 }}
                >
                  <title>{n.label}</title>
                  {label}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
      <div className="flex flex-wrap gap-2 px-1">
        {usedTypes.map((t) => (
          <span key={t} className="inline-flex items-center gap-1.5 text-[10px] text-[var(--color-cream-dim)]">
            <span className="h-2 w-2 rounded-full" style={{ background: NODE_HEX[t] }} />
            {NODE_LABEL[t]}
          </span>
        ))}
      </div>
    </div>
  )
}
