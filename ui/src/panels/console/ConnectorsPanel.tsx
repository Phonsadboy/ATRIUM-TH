import { client } from '../../state/useCompany'
import { Pill, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import type {
  Connector,
  ConnectorKind,
  ConnectorProofStatus,
  ConnectorStatus,
  HostBridgeOpenClawContract,
  HostBridgeOpenClawRequirement,
  HostBridgeParityStatusResponse,
} from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, GhostBtn, useAsync } from './shared'

const STATUS: Record<ConnectorStatus, { label: string; hex: string }> = {
  available: { label: 'พร้อมใช้', hex: ACCENT_HEX.teal },
  configured: { label: 'ตั้งค่าแล้ว', hex: ACCENT_HEX.sky },
  blocked_by_runtime: { label: 'ถูกบล็อกโดย runtime', hex: ACCENT_HEX.coral },
}

const PROOF_STATUS: Record<ConnectorProofStatus, { label: string; hex: string }> = {
  not_required: { label: 'ไม่ต้อง proof', hex: ACCENT_HEX.lavender },
  local_blocked: { label: 'proof ถูกบล็อก', hex: ACCENT_HEX.coral },
  cross_os_unverified: { label: 'รอ proof สอง OS', hex: ACCENT_HEX.amber },
  cross_os_verified: { label: 'proof ผ่าน', hex: ACCENT_HEX.teal },
}

const KIND_LABEL: Record<ConnectorKind, string> = {
  local_file: 'ไฟล์ในเครื่อง',
  git: 'Git',
  http: 'HTTP',
  web: 'เว็บ',
  browser: 'เบราว์เซอร์',
  desktop: 'เดสก์ท็อป',
  sandbox: 'แซนด์บ็อกซ์',
  mcp: 'MCP',
}

function shortValue(value: unknown, chars = 8) {
  return typeof value === 'string' && value.length > chars ? value.slice(0, chars) : String(value ?? '')
}

function proofDetailLine(details?: Record<string, unknown>) {
  if (!details) return ''
  const parts: string[] = []
  const reportGeneratedAt = typeof details.reportGeneratedAt === 'number' ? details.reportGeneratedAt : null
  if (reportGeneratedAt) {
    parts.push(`report ${new Date(reportGeneratedAt).toLocaleString()}`)
  }
  if (details.proofId) {
    parts.push(`proof ${shortValue(details.proofId, 8)}`)
  }
  if (details.parityRunId) {
    parts.push(`run ${shortValue(details.parityRunId, 18)}`)
  }
  if (details.gitHead) {
    parts.push(`git ${shortValue(details.gitHead, 8)}`)
  }
  if (details.sourceFingerprint) {
    parts.push(`source ${shortValue(details.sourceFingerprint, 8)}`)
  }
  const hostName = details.hostName && typeof details.hostName === 'object'
    ? details.hostName as Record<string, unknown>
    : null
  if (hostName?.macos) {
    parts.push(`mac-host ${shortValue(hostName.macos, 12)}`)
  }
  if (hostName?.windows) {
    parts.push(`win-host ${shortValue(hostName.windows, 12)}`)
  }
  const artifactSha = details.artifactSha256 && typeof details.artifactSha256 === 'object'
    ? details.artifactSha256 as Record<string, unknown>
    : null
  if (artifactSha?.macos) {
    parts.push(`mac ${shortValue(artifactSha.macos, 8)}`)
  }
  if (artifactSha?.windows) {
    parts.push(`win ${shortValue(artifactSha.windows, 8)}`)
  }
  return parts.join(' · ')
}

function isRequirementBlocked(requirement: HostBridgeOpenClawRequirement) {
  if (requirement.currentHostApplies === false) return false
  if (requirement.currentReady === false) return true
  if (requirement.registered === false) return true
  if (requirement.proved === false) return true
  if (requirement.required !== false && requirement.ready === false) return true
  return false
}

function requirementName(requirement: HostBridgeOpenClawRequirement) {
  const name = requirement.id || requirement.label || 'unknown'
  const reasons: string[] = []
  if (requirement.degradedByLocalFallback) reasons.push('local fallback only')
  if (requirement.requiresWriteReady && requirement.writeReady === false) reasons.push('write not ready')
  if (requirement.readReady === false) reasons.push('read not ready')
  if (requirement.externalWriteRequires && requirement.externalWriteRequires.length > 0) {
    reasons.push(requirement.externalWriteRequires.slice(0, 2).join(', '))
  }
  return reasons.length > 0 ? `${name} (${reasons.join('; ')})` : name
}

function contractGapGroups(contract?: HostBridgeOpenClawContract | null) {
  if (!contract) return []
  const groups = [
    { id: 'local', label: 'local', requirements: contract.localRequirements },
    { id: 'api', label: 'API', requirements: contract.apiSurfaceRequirements },
    { id: 'report', label: 'report', requirements: contract.reportRequirements },
    { id: 'feature', label: 'feature', requirements: contract.featureRequirements },
    { id: 'proof', label: 'proof', requirements: contract.windowsProofRequirements ?? contract.proofRequirements },
    { id: 'connector', label: 'connector', requirements: contract.connectorRequirements },
  ]
  return groups
    .map((group) => ({
      ...group,
      gaps: (group.requirements ?? [])
        .filter(isRequirementBlocked)
        .map(requirementName),
    }))
    .filter((group) => group.gaps.length > 0)
}

function contractGapNames(contract?: HostBridgeOpenClawContract | null) {
  return contractGapGroups(contract).flatMap((group) => group.gaps)
}

export function ConnectorsPanel() {
  const { data, loading, error, reload } = useAsync<{ connectors: Connector[]; parity: HostBridgeParityStatusResponse }>(
    async () => {
      const [connectors, parity] = await Promise.all([
        client.listConnectors(),
        client.getHostBridgeParity(),
      ])
      return { connectors, parity }
    },
    [],
  )
  const connectors = data?.connectors ?? []
  const parity = data?.parity ?? null
  const parityStatus = parity ? PROOF_STATUS[parity.status] ?? { label: parity.status, hex: ACCENT_HEX.lavender } : null
  const parityDetail = proofDetailLine(parity?.report)
  const parityCommands = parity?.commands ?? {}
  const contract = parity?.contract ?? null
  const contractStatus = contract?.status ? PROOF_STATUS[contract.status] ?? { label: contract.status, hex: ACCENT_HEX.lavender } : null
  const openClawGapGroups = contractGapGroups(contract)
  const openClawGaps = contractGapNames(contract)

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
        <div className="space-y-2.5">
          {parity && parityStatus && (
            <Row accent={parityStatus.hex}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">HostBridge parity</div>
                <Pill color={parityStatus.hex}>{parityStatus.label}</Pill>
              </div>
              <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{parity.summary}</div>
              {parityDetail && (
                <div className="mt-1 text-[10px] text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                  {parityDetail}
                </div>
              )}
              {parity.gaps.length > 0 && (
                <div className="mt-1 text-[10px] break-words [overflow-wrap:anywhere]" style={{ color: withAlpha(parityStatus.hex, 0.9) }}>
                  gap: {parity.gaps.slice(0, 3).join(' · ')}
                </div>
              )}
              {contract && (
                <div className="mt-2 border-t border-[color:var(--color-line-soft)] pt-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-[11px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">OpenClaw Windows contract</div>
                    {contractStatus && <Pill color={contractStatus.hex}>{contractStatus.label}</Pill>}
                  </div>
                  {contract.summary && (
                    <div className="mt-1 text-[10px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{contract.summary}</div>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1">
                    {contract.windowsNativePrimary && <Pill color={ACCENT_HEX.teal}>Windows native primary</Pill>}
                    {contract.windowsNativeOnly && <Pill color={ACCENT_HEX.sky}>native only</Pill>}
                    {contract.noSilentDegradation && <Pill color={ACCENT_HEX.amber}>no silent degradation</Pill>}
                  </div>
                  {openClawGaps.length > 0 && (
                    <div className="mt-1 grid gap-1 text-[10px] break-words [overflow-wrap:anywhere]" style={{ color: withAlpha(contractStatus?.hex ?? ACCENT_HEX.amber, 0.9) }}>
                      {openClawGapGroups.slice(0, 5).map((group) => (
                        <div key={group.id}>
                          {group.label}: {group.gaps.slice(0, 4).join(' · ')}
                          {group.gaps.length > 4 ? ` · +${group.gaps.length - 4}` : ''}
                        </div>
                      ))}
                    </div>
                  )}
                  {contract.osBoundaries && contract.osBoundaries.length > 0 && (
                    <div className="mt-1 text-[10px] text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]">
                      boundary: {contract.osBoundaries.slice(0, 2).join(' · ')}
                    </div>
                  )}
                </div>
              )}
              {(parityCommands.parityRunId || parityCommands.sourceFingerprint || parityCommands.sourceManifestSha256 || parityCommands.sourceFileCount || parityCommands.macosSourceValidate || parityCommands.macosProbe || parityCommands.macosArtifactValidate || parityCommands.windowsSourceValidate || parityCommands.windowsProbe || parityCommands.windowsLiveProofRunner || parityCommands.windowsArtifactValidateOnWindows || parityCommands.windowsArtifactSource || parityCommands.windowsArtifactLocal || parityCommands.windowsArtifactCopyHint || parityCommands.windowsArtifactValidateLocal || parityCommands.automationReport || parityCommands.report || parityCommands.verify || parityCommands.legacyParityReport) && (
                <div className="mt-2 grid gap-1 text-[10px] text-[var(--color-cream-faint)]">
                  {parityCommands.parityRunId && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      run-id: <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.parityRunId}</span>
                    </div>
                  )}
                  {parityCommands.sourceFingerprint && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      source: <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.sourceFingerprint}</span>
                    </div>
                  )}
                  {parityCommands.sourceManifestSha256 && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      manifest: <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.sourceManifestSha256}</span>
                    </div>
                  )}
                  {parityCommands.sourceFileCount && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      files: <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.sourceFileCount}</span>
                    </div>
                  )}
                  {parityCommands.macosSourceValidate && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.macosSourceValidate}
                    </div>
                  )}
                  {parityCommands.macosProbe && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.macosProbe}
                    </div>
                  )}
                  {parityCommands.macosArtifactValidate && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.macosArtifactValidate}
                    </div>
                  )}
                  {parityCommands.windowsSourceValidate && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.windowsSourceValidate}
                    </div>
                  )}
                  {parityCommands.windowsProbe && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.windowsProbe}
                    </div>
                  )}
                  {parityCommands.windowsLiveProofRunner && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.windowsLiveProofRunner}
                    </div>
                  )}
                  {parityCommands.windowsArtifactValidateOnWindows && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.windowsArtifactValidateOnWindows}
                    </div>
                  )}
                  {parityCommands.windowsArtifactSource && parityCommands.windowsArtifactLocal && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      Windows artifact: <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.windowsArtifactSource}</span>
                      {' -> '}
                      <span style={{ fontFamily: 'var(--font-mono)' }}>{parityCommands.windowsArtifactLocal}</span>
                    </div>
                  )}
                  {parityCommands.windowsArtifactCopyHint && (
                    <div className="break-words [overflow-wrap:anywhere]">
                      {parityCommands.windowsArtifactCopyHint}
                    </div>
                  )}
                  {parityCommands.windowsArtifactValidateLocal && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.windowsArtifactValidateLocal}
                    </div>
                  )}
                  {(parityCommands.automationReport || parityCommands.report) && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.automationReport || parityCommands.report}
                    </div>
                  )}
                  {parityCommands.verify && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.verify}
                    </div>
                  )}
                  {parityCommands.legacyParityReport && (
                    <div className="break-words [overflow-wrap:anywhere]" style={{ fontFamily: 'var(--font-mono)' }}>
                      {parityCommands.legacyParityReport}
                    </div>
                  )}
                </div>
              )}
            </Row>
          )}
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {connectors.map((c: Connector) => {
            const st = STATUS[c.status] ?? { label: c.status, hex: ACCENT_HEX.lavender }
            const tools = c.tools ?? []
            const requires = c.requires ?? []
            const externalWriteRequires = c.externalWriteRequires ?? []
            const proofGaps = c.proofGaps ?? []
            const proofDetail = proofDetailLine(c.proofDetails)
            const proof = c.proofStatus && c.proofStatus !== 'not_required'
              ? PROOF_STATUS[c.proofStatus] ?? { label: c.proofStatus, hex: ACCENT_HEX.lavender }
              : null
            return (
              <Row key={c.id} accent={st.hex}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 text-[13px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{c.name}</div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1">
                    {proof && <Pill color={proof.hex}>{proof.label}</Pill>}
                    <Pill color={st.hex}>{st.label}</Pill>
                  </div>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-cream-faint)]">
                  <Pill color={ACCENT_HEX.lavender}>{KIND_LABEL[c.kind] ?? c.kind}</Pill>
                  <Pill color={c.readReady ? ACCENT_HEX.teal : ACCENT_HEX.coral}>{c.readReady ? 'อ่านพร้อม' : 'อ่านไม่พร้อม'}</Pill>
                  <Pill color={c.writeReady ? ACCENT_HEX.teal : ACCENT_HEX.amber}>{c.writeReady ? 'เขียนพร้อม' : 'เขียนยังไม่พร้อม'}</Pill>
                  {c.localFallback && <Pill color={ACCENT_HEX.honey}>local fallback</Pill>}
                  {c.runtimeStatus && <span>· {c.runtimeStatus}</span>}
                </div>
                {c.description && <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{c.description}</div>}
                {tools.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {tools.slice(0, 8).map((t) => (
                      <span key={t} className="rounded px-1.5 py-0.5 text-[9px]" style={{ color: 'var(--color-cream-dim)', background: 'var(--color-surface-3)', fontFamily: 'var(--font-mono)' }}>{t}</span>
                    ))}
                    {tools.length > 8 && <span className="text-[9px] text-[var(--color-cream-faint)]">+{tools.length - 8}</span>}
                  </div>
                )}
                {requires.length > 0 && (
                  <div className="mt-1 text-[10px]" style={{ color: withAlpha(ACCENT_HEX.amber, 0.9) }}>ต้องการ: {requires.join(', ')}</div>
                )}
                {proof && (
                  <div className="mt-1 text-[10px] text-[var(--color-cream-faint)] break-words [overflow-wrap:anywhere]">
                    {c.proofSummary && <div>{c.proofSummary}</div>}
                    {proofDetail && (
                      <div className="mt-0.5" style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>{proofDetail}</div>
                    )}
                    {proofGaps.length > 0 && (
                      <div style={{ color: withAlpha(proof.hex, 0.9) }}>
                        gap: {proofGaps.slice(0, 2).join(' · ')}
                      </div>
                    )}
                  </div>
                )}
                {externalWriteRequires.length > 0 && (
                  <div className="mt-1 text-[10px]" style={{ color: withAlpha(ACCENT_HEX.amber, 0.9) }}>เขียนภายนอกต้องการ: {externalWriteRequires.join(', ')}</div>
                )}
              </Row>
            )
            })}
          </div>
        </div>
      )}
    </Section>
  )
}
