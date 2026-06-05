import { client } from '../../state/useCompany'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { KnowledgeDebtDepartment } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, GhostBtn, useAsync, useDepts } from './shared'

function healthHex(h: number): string {
  if (h >= 0.75) return ACCENT_HEX.teal
  if (h >= 0.5) return ACCENT_HEX.amber
  return ACCENT_HEX.coral
}

const METRICS: { key: keyof KnowledgeDebtDepartment; label: string }[] = [
  { key: 'stale', label: 'เก่า/ค้าง' },
  { key: 'duplicate', label: 'ซ้ำ' },
  { key: 'conflicting', label: 'ขัดแย้ง' },
  { key: 'orphaned', label: 'ลอย' },
  { key: 'unsourced', label: 'ไม่มีที่มา' },
]

export function KnowledgeDebtPanel() {
  const { data, loading, error, reload } = useAsync(() => client.getKnowledgeDebt(), [])
  const graph = useAsync(() => client.getGraphHealth(), [])
  const runtime = useAsync(() => client.getHealth(), [])
  const { label } = useDepts()
  const depts = data?.departments ?? []

  return (
    <Section title="หนี้ความรู้ (Knowledge Debt)" hint="สุขภาพคลังความรู้ของแต่ละแผนก" actions={<GhostBtn onClick={() => { reload(); graph.reload(); runtime.reload() }}>รีเฟรช</GhostBtn>}>
      {(graph.data || runtime.data) && (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-2 text-[11px]" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
          {runtime.data && (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: runtime.data.memory.vectorSearch === 'pgvector' ? ACCENT_HEX.teal : ACCENT_HEX.amber }} />
                <span className="text-[var(--color-cream-dim)]">RAG:</span>
                <span className="text-[var(--color-cream)]">{runtime.data.memory.vectorSearch}</span>
              </span>
              <span className="text-[var(--color-cream-faint)]">storage: {runtime.data.memory.storage}</span>
              <span className="text-[var(--color-cream-faint)]">
                · embeddings: {runtime.data.memory.embeddings.mode ? `${runtime.data.memory.embeddings.mode} · ` : ''}{runtime.data.memory.embeddings.provider}
                {runtime.data.memory.embeddings.dim ? ` · ${runtime.data.memory.embeddings.dim}d` : ''}
              </span>
              {runtime.data.memory.embeddings.error && <span style={{ color: ACCENT_HEX.coral }}>· {runtime.data.memory.embeddings.error}</span>}
            </>
          )}
          {graph.data && (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: graph.data.enabled && !graph.data.error ? ACCENT_HEX.teal : ACCENT_HEX.coral }} />
                <span className="text-[var(--color-cream-dim)]">กราฟ:</span>
                <span className="text-[var(--color-cream)]">{graph.data.enabled ? 'เปิดใช้งาน' : 'ปิด'}</span>
              </span>
              <span className="text-[var(--color-cream-faint)]">· graph backend: {graph.data.backend}</span>
              {graph.data.error && <span style={{ color: ACCENT_HEX.coral }}>· {graph.data.error}</span>}
            </>
          )}
        </div>
      )}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? <Loading /> : depts.length === 0 && !error ? <Empty text="ยังไม่มีข้อมูลหนี้ความรู้" /> : (
        <div className="space-y-2.5">
          {depts.map((d) => {
            const hex = healthHex(d.health)
            return (
              <Row key={d.departmentId} accent={hex}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{label(d.departmentId)}</div>
                  <Pill color={hex}>สุขภาพ {Math.round(d.health * 100)}%</Pill>
                </div>
                <div className="mt-2 grid grid-cols-5 gap-1.5">
                  {METRICS.map((m) => {
                    const v = d[m.key] as number
                    return (
                      <div key={m.key} className="rounded-lg border p-1.5 text-center" style={{ borderColor: 'var(--color-line-soft)', background: v > 0 ? withAlpha(ACCENT_HEX.coral, 0.08) : 'transparent' }}>
                        <div className="text-[14px] font-semibold tabular-nums" style={{ fontFamily: 'var(--font-mono)', color: v > 0 ? ACCENT_HEX.coral : 'var(--color-cream-faint)' }}>{v}</div>
                        <div className="text-[9px] text-[var(--color-cream-faint)]">{m.label}</div>
                      </div>
                    )
                  })}
                </div>
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}
