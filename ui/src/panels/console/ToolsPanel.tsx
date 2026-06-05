import { useMemo, useState } from 'react'
import { client, useSelector } from '../../state/useCompany'
import { Field, Pill, inputClass, withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { relTime } from '../../lib/format'
import { isExec } from '../../lib/threads'
import { isHumanApproval } from '../../lib/approvals'
import type { ToolCatalogItem, ToolName, ToolRun, ToolRunStatus, ToolRiskClass, Approval, PolicyMode } from '../../contract/types'
import { Section, Loading, Empty, ErrorNote, Row, PrimaryBtn, GhostBtn, Tabs, FormCard, SubLabel, useAsync, useDepts, useNow } from './shared'

type Tab = 'run' | 'runs' | 'approvals'
const TABS: { id: Tab; label: string }[] = [
  { id: 'run', label: 'แค็ตตาล็อก/รัน' },
  { id: 'runs', label: 'ประวัติการรัน' },
  { id: 'approvals', label: 'รออนุมัติ' },
]

const LEGACY_TOOLS = new Set<ToolName>(['read_file', 'write_file', 'http_get', 'run_command'])
const TOOL_LABEL: Partial<Record<ToolName, string>> = {
  read_file: 'อ่านไฟล์',
  write_file: 'เขียนไฟล์',
  http_get: 'เรียก HTTP',
  run_command: 'รันคำสั่ง',
  'fs.list': 'ดูรายการไฟล์',
  'fs.read': 'อ่านไฟล์',
  'fs.write': 'เขียนไฟล์',
  'fs.patch': 'แก้บางส่วน',
  'fs.delete': 'ลบไฟล์',
  'shell.exec': 'รันคำสั่ง',
  'sandbox.exec': 'รันใน sandbox',
  'git.status': 'Git status',
  'git.diff': 'Git diff',
  'git.commit': 'Git commit',
  'git.push': 'Git push',
  'browser.profiles': 'โปรไฟล์เบราว์เซอร์',
  'browser.open': 'เปิดเบราว์เซอร์',
  'browser.snapshot': 'อ่านหน้าเว็บ',
  'browser.act': 'สั่งงานหน้าเว็บ',
  'browser.screenshot': 'จับภาพเบราว์เซอร์',
  'browser.click': 'คลิกเบราว์เซอร์',
  'browser.scroll': 'เลื่อนเบราว์เซอร์',
  'desktop.screenshot': 'จับภาพหน้าจอ',
  'desktop.apps': 'รายการแอป',
  'desktop.snapshot': 'อ่านหน้าแอป',
  'desktop.act': 'สั่งงานแอป',
  'desktop.open_app': 'เปิดแอป',
  'desktop.activate_app': 'โฟกัสแอป',
  'desktop.quit_app': 'ปิดแอป',
  'desktop.click': 'คลิกหน้าจอ',
  'http.get': 'HTTP GET',
  'http.post': 'HTTP POST',
  'mcp.call': 'MCP call',
  'notify.send': 'ส่งแจ้งเตือน',
  'scheduler.create': 'สร้าง trigger',
  'logs.query': 'อ่าน log',
  'logs.note': 'บันทึก log',
}

const RUN_STATUS: Record<ToolRunStatus, { label: string; hex: string }> = {
  queued: { label: 'เข้าคิว', hex: ACCENT_HEX.lavender },
  running: { label: 'กำลังรัน', hex: ACCENT_HEX.sky },
  pending_approval: { label: 'รออนุมัติ', hex: ACCENT_HEX.coral },
  succeeded: { label: 'สำเร็จ', hex: ACCENT_HEX.teal },
  completed: { label: 'สำเร็จ', hex: ACCENT_HEX.teal },
  failed: { label: 'ล้มเหลว', hex: ACCENT_HEX.coral },
  cancelled: { label: 'ยกเลิก', hex: ACCENT_HEX.honey },
  blocked: { label: 'ถูกบล็อก', hex: ACCENT_HEX.coral },
}

const POLICY_LABEL: Record<PolicyMode, { label: string; hex: string }> = {
  approve_everything: { label: 'Full Auto (คำขอเดิม)', hex: ACCENT_HEX.coral },
  critical_only: { label: 'Full Auto (คำขอเดิม)', hex: ACCENT_HEX.coral },
  full_auto: { label: 'Full Auto', hex: ACCENT_HEX.coral },
}

function riskHex(r?: ToolRiskClass | null): string {
  if (!r) return ACCENT_HEX.lavender
  if (['destructive', 'privileged', 'credential', 'command'].includes(r)) return ACCENT_HEX.coral
  if (['host_write', 'external_send', 'network'].includes(r)) return ACCENT_HEX.amber
  return ACCENT_HEX.teal
}

function toolLabel(tool: ToolName | string): string {
  return TOOL_LABEL[tool as ToolName] ?? tool
}

function defaultArgsText(tool: ToolName): string {
  const args: Record<string, unknown> =
    tool === 'fs.list' ? { path: '.' }
    : tool === 'fs.read' ? { path: 'README.md' }
    : tool === 'fs.write' ? { path: 'notes.txt', text: '' }
    : tool === 'fs.patch' ? { path: 'notes.txt', oldText: '', newText: '' }
    : tool === 'fs.delete' ? { path: 'notes.txt', recursive: false }
    : tool === 'shell.exec' || tool === 'run_command' ? { command: ['pwd'], cwd: '.' }
    : tool === 'sandbox.exec' ? { command: ['pwd'], image: 'python:3.12-slim', network: false }
    : tool === 'git.status' ? { cwd: '.' }
    : tool === 'git.diff' ? { cwd: '.', staged: false }
    : tool === 'git.commit' ? { cwd: '.', message: '' }
    : tool === 'git.push' ? { cwd: '.', remote: 'origin' }
    : tool === 'browser.profiles' ? {}
    : tool === 'browser.open' ? { url: 'http://127.0.0.1:5173' }
    : tool === 'browser.snapshot' ? { url: 'http://127.0.0.1:5173', profile: 'atrium', maxElements: 80 }
    : tool === 'browser.act' ? { ref: 'b1', action: 'click', profile: 'atrium' }
    : tool === 'browser.screenshot' ? { path: 'browser.png', profile: 'atrium' }
    : tool === 'browser.click' || tool === 'desktop.click' ? { x: 0, y: 0 }
    : tool === 'browser.scroll' ? { direction: 'down', amount: 1, unit: 'page' }
    : tool === 'desktop.screenshot' ? { path: 'screenshot.png' }
    : tool === 'desktop.apps' ? { includeRunning: true, includeInstalled: true, limit: 80 }
    : tool === 'desktop.snapshot' ? { maxElements: 120, maxDepth: 4 }
    : tool === 'desktop.act' ? { ref: 'd1', action: 'click', snapshotAfter: true }
    : tool === 'desktop.open_app' ? { appName: 'TextEdit' }
    : tool === 'desktop.activate_app' ? { appName: 'TextEdit' }
    : tool === 'desktop.quit_app' ? { appName: 'TextEdit', force: false }
    : tool === 'http.get' || tool === 'http_get' ? { url: 'https://example.com' }
    : tool === 'http.post' ? { url: 'https://example.com', json: {} }
    : tool === 'mcp.call' ? { server: '', tool: '', arguments: {} }
    : tool === 'notify.send' ? { title: '', body: '' }
    : tool === 'scheduler.create' ? { title: '', kind: 'event', target: '', event: '' }
    : tool === 'logs.query' ? { limit: 50 }
    : tool === 'logs.note' ? { body: '', author: 'owner' }
    : {}
  return JSON.stringify(args, null, 2)
}

export function ToolsPanel() {
  const [tab, setTab] = useState<Tab>('run')
  const running = useSelector((s) => s.running)

  return (
    <Section
      title="เครื่องมือ (Tools)"
      hint="รันเครื่องมือผ่าน guardrail · ดูประวัติ · อนุมัติงานเสี่ยง"
      actions={
        running ? (
          <GhostBtn tint={ACCENT_HEX.coral} onClick={() => void client.toolsKillSwitch().catch(() => undefined)}>หยุดเครื่องมือทั้งหมด</GhostBtn>
        ) : (
          <GhostBtn tint={ACCENT_HEX.teal} onClick={() => void client.toolsResume().catch(() => undefined)}>เปิดใช้เครื่องมืออีกครั้ง</GhostBtn>
        )
      }
    >
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {tab === 'run' && <CatalogAndRun />}
      {tab === 'runs' && <RunsList />}
      {tab === 'approvals' && <ToolApprovals />}
    </Section>
  )
}

function CatalogAndRun() {
  const { data, loading, error, reload } = useAsync(() => client.getToolCatalog(), [])
  const mode = useSelector((s) => s.permissionPolicy?.mode ?? 'full_auto')
  const policy = POLICY_LABEL[mode] ?? POLICY_LABEL.full_auto
  const catalog = data ?? []
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between border px-3 py-2.5" style={{ borderRadius: 12, borderColor: withAlpha(policy.hex, 0.28), background: withAlpha(policy.hex, 0.08) }}>
        <span className="text-[11px] font-medium text-[var(--color-cream-dim)]">สิทธิ์กลางของบริษัท</span>
        <Pill color={policy.hex}>{policy.label}</Pill>
      </div>
      <RunForm catalog={catalog} />
      {error && <ErrorNote error={error} onRetry={reload} />}
      {loading && !data ? (
        <Loading />
      ) : (
        <div className="space-y-2">
          <SubLabel>แค็ตตาล็อกเครื่องมือ · {catalog.length}</SubLabel>
          {catalog.map((c) => (
            <div key={c.tool} className="border p-2.5" style={{ borderRadius: 12, borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}>
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{toolLabel(c.tool)} <span className="text-[var(--color-cream-faint)]">· {c.tool}</span></span>
                <Pill color={riskHex(c.riskClass)}>{c.riskClass}</Pill>
              </div>
              <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{c.description}</div>
              <div className="mt-1 flex gap-2 text-[10px] text-[var(--color-cream-faint)]">
                {c.mutatesState && <span>เปลี่ยนสถานะ</span>}
                {c.externalSystem && <span>· ระบบภายนอก</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RunForm({ catalog }: { catalog: ToolCatalogItem[] }) {
  const depts = useSelector((s) => s.departments.map((d) => ({ id: d.id, name: d.name, emoji: d.emoji })))
  const defaultDeptId = depts.find((d) => !isExec(d.id))?.id ?? depts[0]?.id ?? ''
  const availableTools = useMemo(() => {
    const canonical = catalog.filter((item) => !LEGACY_TOOLS.has(item.tool))
    return canonical.length ? canonical : catalog
  }, [catalog])
  const [tool, setTool] = useState<ToolName>('fs.list')
  const effectiveTool = availableTools.some((item) => item.tool === tool)
    ? tool
    : availableTools[0]?.tool ?? tool
  const [deptId, setDeptId] = useState('')
  const effectiveDeptId = deptId || defaultDeptId
  const [argsText, setArgsText] = useState(defaultArgsText('fs.list'))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [argErr, setArgErr] = useState<string | null>(null)

  const selectedTool = availableTools.find((item) => item.tool === effectiveTool)

  const submit = () => {
    let args: Record<string, unknown>
    try {
      args = argsText.trim() ? JSON.parse(argsText) : {}
      setArgErr(null)
    } catch {
      setArgErr('args ไม่ใช่ JSON ที่ถูกต้อง')
      return
    }
    if (!effectiveDeptId || busy) return
    setBusy(true)
    setResult(null)
    void client
      .runTool({ tool: effectiveTool, departmentId: effectiveDeptId, args })
      .then((r) => {
        setBusy(false)
        if (r.approval) setResult('ต้องรออนุมัติก่อน: ดูที่แท็บ "รออนุมัติ"')
        else if (r.run.status === 'failed' || r.run.status === 'blocked' || r.run.status === 'cancelled') {
          setResult(`${RUN_STATUS[r.run.status].label}: ${r.run.error ?? '-'}`)
        } else {
          setResult(`${RUN_STATUS[r.run.status]?.label ?? r.run.status}\n${JSON.stringify(r.run.result ?? {}, null, 2)}`)
        }
      })
      .catch((e) => { setBusy(false); setResult(e instanceof Error ? e.message : String(e)) })
  }

  return (
    <FormCard title="รันเครื่องมือ">
      <div className="grid grid-cols-2 gap-3">
        <Field label="เครื่องมือ">
          <select
            className={inputClass}
            style={{ borderColor: 'var(--color-line-soft)' }}
            value={effectiveTool}
            onChange={(e) => {
              const nextTool = e.target.value as ToolName
              setTool(nextTool)
              setArgsText(defaultArgsText(nextTool))
            }}
          >
            {availableTools.map((item) => <option key={item.tool} value={item.tool}>{toolLabel(item.tool)}</option>)}
          </select>
        </Field>
        <Field label="ในนามแผนก">
          <select className={inputClass} style={{ borderColor: 'var(--color-line-soft)' }} value={effectiveDeptId} onChange={(e) => setDeptId(e.target.value)}>
            {depts.map((d) => <option key={d.id} value={d.id}>{d.emoji} {d.name}</option>)}
          </select>
        </Field>
      </div>
      {selectedTool && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-cream-faint)]">
          <Pill color={riskHex(selectedTool.riskClass)}>{selectedTool.riskClass}</Pill>
          <span>{selectedTool.tool}</span>
          {selectedTool.externalSystem && <span>· external</span>}
          {selectedTool.mutatesState && <span>· mutates</span>}
        </div>
      )}
      <div className="mt-2">
        <Field label="args (JSON)">
          <textarea className={inputClass} style={{ borderColor: argErr ? withAlpha(ACCENT_HEX.coral, 0.6) : 'var(--color-line-soft)', minHeight: 80, resize: 'vertical', fontFamily: 'var(--font-mono)' }} value={argsText} onChange={(e) => setArgsText(e.target.value)} />
        </Field>
        {argErr && <div className="mt-1 text-[10px]" style={{ color: ACCENT_HEX.coral }}>{argErr}</div>}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-[11px] text-[var(--color-cream-faint)]">
          Full Auto: เครื่องมือรันทันทีเมื่อ runtime พร้อม
        </div>
        <PrimaryBtn onClick={submit} disabled={!effectiveDeptId || busy}>{busy ? 'กำลังรัน…' : 'รัน'}</PrimaryBtn>
      </div>
      {result && (
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg p-2 text-[11px]" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>{result}</pre>
      )}
    </FormCard>
  )
}

function RunsList() {
  const { data, loading, error, reload } = useAsync(() => client.listToolRuns({ limit: 100 }), [])
  const { label } = useDepts()
  const now = useNow()
  const runs = data ?? []
  const [openId, setOpenId] = useState<string | null>(null)

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (runs.length === 0) return <Empty text="ยังไม่มีประวัติการรันเครื่องมือ" />

  return (
    <div className="space-y-2">
      {runs.map((r: ToolRun) => {
        const st = RUN_STATUS[r.status] ?? { label: r.status, hex: ACCENT_HEX.lavender }
        const open = openId === r.id
        return (
          <Row key={r.id} accent={st.hex}>
            <button type="button" onClick={() => setOpenId(open ? null : r.id)} className="block w-full text-left">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-medium text-[var(--color-cream)]">{toolLabel(r.tool)}</span>
                <div className="flex items-center gap-1.5">
                  {r.riskClass && <Pill color={riskHex(r.riskClass)}>{r.riskClass}</Pill>}
                  <Pill color={st.hex}>{st.label}</Pill>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-[var(--color-cream-faint)]">
                <span>{label(r.departmentId)}</span>
                {r.policyDecision && <span>· {r.policyDecision}</span>}
                <span className="ml-auto">{relTime(r.createdAt, now)}ที่แล้ว</span>
              </div>
            </button>
            {open && (
              <div className="mt-2 space-y-1.5 border-t pt-2" style={{ borderColor: 'var(--color-line-soft)' }}>
                <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded p-1.5 text-[10px]" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}>
                  args: {JSON.stringify(r.args, null, 2)}
                </pre>
                {r.result && (
                  <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded p-1.5 text-[10px]" style={{ background: 'var(--color-surface)', fontFamily: 'var(--font-mono)', color: ACCENT_HEX.teal }}>
                    result: {JSON.stringify(r.result, null, 2)}
                  </pre>
                )}
                {r.error && <div className="text-[11px]" style={{ color: ACCENT_HEX.coral }}>{r.error}</div>}
                {r.status === 'pending_approval' && (
                  <button type="button" onClick={() => void client.cancelToolRun(r.id).then(reload).catch(() => undefined)} className="rounded-lg border px-2 py-0.5 text-[10px]" style={{ borderColor: withAlpha(ACCENT_HEX.coral, 0.4), color: ACCENT_HEX.coral }}>ยกเลิกการรัน</button>
                )}
              </div>
            )}
          </Row>
        )
      })}
    </div>
  )
}

function ToolApprovals() {
  const { data, loading, error, reload } = useAsync(() => client.listToolApprovals({ limit: 100 }), [])
  const { label } = useDepts()
  const approvals = (data ?? []).filter(isHumanApproval)
  const pending = approvals.filter((a) => a.status === 'pending')

  if (loading && !data) return <Loading />
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (approvals.length === 0) return <Empty text="ไม่มีคำขออนุมัติเครื่องมือ" />

  const resolve = (a: Approval, decision: 'approved' | 'rejected') =>
    void client.resolveToolApproval(a.id, decision).then(reload).catch(() => undefined)

  return (
    <div className="space-y-2.5">
      {pending.length === 0 && <div className="text-[11px] text-[var(--color-cream-faint)]">ไม่มีคำขอที่รออยู่</div>}
      {approvals.map((a) => (
        <Row key={a.id} accent={a.status === 'pending' ? ACCENT_HEX.coral : undefined}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-[12px] font-medium text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">{a.title}</div>
            <Pill color={a.status === 'pending' ? ACCENT_HEX.coral : a.status === 'approved' ? ACCENT_HEX.teal : ACCENT_HEX.honey}>
              {a.status === 'pending' ? 'รออนุมัติ' : a.status === 'approved' ? 'อนุมัติแล้ว' : 'ปฏิเสธแล้ว'}
            </Pill>
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-cream-dim)] break-words [overflow-wrap:anywhere]">{a.detail}</div>
          <div className="mt-1 text-[10px] text-[var(--color-cream-faint)]">{label(a.departmentId)}</div>
          {a.status === 'pending' && (
            <div className="mt-2 flex gap-2">
              <button type="button" onClick={() => resolve(a, 'approved')} className="flex-1 rounded-xl py-1.5 text-xs font-semibold" style={{ background: withAlpha(ACCENT_HEX.teal, 0.18), color: ACCENT_HEX.teal, border: `1px solid ${withAlpha(ACCENT_HEX.teal, 0.4)}` }}>อนุมัติ</button>
              <button type="button" onClick={() => resolve(a, 'rejected')} className="flex-1 rounded-xl py-1.5 text-xs font-semibold" style={{ background: withAlpha(ACCENT_HEX.coral, 0.14), color: ACCENT_HEX.coral, border: `1px solid ${withAlpha(ACCENT_HEX.coral, 0.35)}` }}>ปฏิเสธ</button>
            </div>
          )}
        </Row>
      ))}
    </div>
  )
}
