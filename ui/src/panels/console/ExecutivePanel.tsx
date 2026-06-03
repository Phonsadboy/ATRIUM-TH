import { client } from '../../state/useCompany'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { money } from '../../lib/format'
import { Section, Loading, Empty, ErrorNote, GhostBtn, useAsync } from './shared'

// GET /api/executive is read-only on the backend — this is an inspector.
export function ExecutivePanel() {
  const { data, loading, error, reload } = useAsync(() => client.getExecutive(), [])

  return (
    <Section title="ผู้บริหาร (Executive)" hint="เอเจนต์ผู้บริหารที่ประสานงานทั้งบริษัท" actions={<GhostBtn onClick={reload}>รีเฟรช</GhostBtn>}>
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : !data ? (
        <Empty text="ไม่พบข้อมูลผู้บริหาร" />
      ) : (
        <div className="space-y-3">
          <div className="rounded-[14px] border p-4" style={{ borderColor: withAlpha(ACCENT_HEX.amber, 0.3), background: `linear-gradient(150deg, ${withAlpha(ACCENT_HEX.amber, 0.1)}, transparent 70%)` }}>
            <div className="flex items-center justify-between">
              <div className="min-w-0 text-base break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}>{data.agentName}</div>
              <Pill color={data.autonomy ? ACCENT_HEX.teal : ACCENT_HEX.lavender}>{data.autonomy ? 'ทำงานเอง' : 'รอสั่ง'}</Pill>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[11px]">
              <Info label="Provider" value={data.providerId} />
              <Info label="โมเดล" value={data.model} mono />
              <Info label="ระดับ Think" value={data.thinkingEffort} />
              <Info label="งบรายวัน" value={money(data.dailyBudgetUsd)} mono />
            </div>
          </div>
          {data.tools.length > 0 && (
            <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
              <div className="mb-1 text-[11px] font-semibold text-[var(--color-cream-dim)]">เครื่องมือ</div>
              <div className="flex flex-wrap gap-1.5">{data.tools.map((t) => <Pill key={t} color={ACCENT_HEX.sky}>{t}</Pill>)}</div>
            </div>
          )}
          {data.systemPrompt && (
            <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
              <div className="mb-1 text-[11px] font-semibold text-[var(--color-cream-dim)]">System prompt</div>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-[var(--color-cream-dim)]">{data.systemPrompt}</pre>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-[var(--color-cream-faint)]">{label}</div>
      <div className={'text-[var(--color-cream)] break-words [overflow-wrap:anywhere] ' + (mono ? 'tabular-nums' : '')} style={mono ? { fontFamily: 'var(--font-mono)' } : undefined}>{value}</div>
    </div>
  )
}
