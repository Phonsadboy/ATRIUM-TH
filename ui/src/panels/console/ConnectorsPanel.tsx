import { client } from '../../state/useCompany'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type { Connector, ConnectorKind, ConnectorStatus } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, GhostBtn, useAsync } from './shared'

const STATUS: Record<ConnectorStatus, { label: string; hex: string }> = {
  available: { label: 'พร้อมใช้', hex: ACCENT_HEX.teal },
  configured: { label: 'ตั้งค่าแล้ว', hex: ACCENT_HEX.sky },
  blocked_by_runtime: { label: 'ถูกบล็อกโดย runtime', hex: ACCENT_HEX.coral },
}

const KIND_LABEL: Record<ConnectorKind, string> = {
  local_file: 'ไฟล์ในเครื่อง',
  git: 'Git',
  http: 'HTTP',
  browser: 'เบราว์เซอร์',
  desktop: 'เดสก์ท็อป',
  sandbox: 'แซนด์บ็อกซ์',
  mcp: 'MCP',
}

export function ConnectorsPanel() {
  const { data, loading, error, reload } = useAsync(() => client.listConnectors(), [])
  const connectors = data ?? []

  return (
    <Section
      title="ตัวเชื่อมต่อ (Connectors)"
      hint="ช่องทางที่เอเจนต์ใช้ทำงานกับโลกภายนอก — ไฟล์ Git เว็บ เดสก์ท็อป MCP"
      actions={<GhostBtn onClick={reload}>รีเฟรช</GhostBtn>}
    >
      {error && <ErrorNote error={`${error} — อาจยังไม่พร้อมบน backend เวอร์ชันนี้`} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : connectors.length === 0 && !error ? (
        <Empty text="ยังไม่มีตัวเชื่อมต่อ" />
      ) : (
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {connectors.map((c: Connector) => {
            const st = STATUS[c.status] ?? { label: c.status, hex: ACCENT_HEX.lavender }
            return (
              <Row key={c.id} accent={st.hex}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{c.name}</div>
                  <Pill color={st.hex}>{st.label}</Pill>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-cream-faint)]">
                  <Pill color={ACCENT_HEX.lavender}>{KIND_LABEL[c.kind] ?? c.kind}</Pill>
                  <Pill color={c.readReady ? ACCENT_HEX.teal : ACCENT_HEX.coral}>{c.readReady ? 'อ่านพร้อม' : 'อ่านไม่พร้อม'}</Pill>
                  <Pill color={c.writeReady ? ACCENT_HEX.teal : ACCENT_HEX.amber}>{c.writeReady ? 'เขียนพร้อม' : 'เขียนยังไม่พร้อม'}</Pill>
                  {c.localFallback && <Pill color={ACCENT_HEX.honey}>local fallback</Pill>}
                  {c.runtimeStatus && <span>· {c.runtimeStatus}</span>}
                </div>
                {c.description && <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{c.description}</div>}
                {c.tools.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {c.tools.slice(0, 8).map((t) => (
                      <span key={t} className="rounded px-1.5 py-0.5 text-[9px]" style={{ color: 'var(--color-cream-dim)', background: 'var(--color-surface-3)', fontFamily: 'var(--font-mono)' }}>{t}</span>
                    ))}
                    {c.tools.length > 8 && <span className="text-[9px] text-[var(--color-cream-faint)]">+{c.tools.length - 8}</span>}
                  </div>
                )}
                {c.requires.length > 0 && (
                  <div className="mt-1 text-[10px]" style={{ color: withAlpha(ACCENT_HEX.amber, 0.9) }}>ต้องการ: {c.requires.join(', ')}</div>
                )}
                {c.externalWriteRequires.length > 0 && (
                  <div className="mt-1 text-[10px]" style={{ color: withAlpha(ACCENT_HEX.amber, 0.9) }}>เขียนภายนอกต้องการ: {c.externalWriteRequires.join(', ')}</div>
                )}
              </Row>
            )
          })}
        </div>
      )}
    </Section>
  )
}
