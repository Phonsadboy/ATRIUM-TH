import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useSelector, client, shallowArrayEqual } from '../state/useCompany'
import { useUI } from '../state/ui'
import type { RightTab } from '../state/ui'
import { Avatar, Dot, withAlpha } from '../components/primitives'
import { Icon } from '../components/Icon'
import { ChatPanel } from './ChatPanel'
import { DeptTasks } from './DeptTasks'
import { ExecutiveWorkMonitor } from './ExecutiveWorkMonitor'
import { MemoryViewer } from './MemoryViewer'
import { ACCENT_HEX, STATE_HEX, STATE_LABEL } from '../lib/visuals'
import {
  AI_PROVIDERS,
  MODELS,
  PROVIDER_LIST,
  THINKING_EFFORTS,
  modelEffortLabel,
  modelRouteLabel,
  modelsForProvider,
  providerRouteLabel,
  thinkingEffortsForModel,
} from '../contract/models'
import { EXEC_ID, threadIdFor, isExec } from '../lib/threads'
import { useIsDesktop } from '../lib/useMedia'
import { useSpeedCatalog } from '../state/useCatalog'
import { PanelStrip } from '../components/PanelStrip'
import type { AiProviderId, ModelId, ProviderAuthReferenceResponse, ProviderAuthStatusResponse, ThinkingEffort } from '../contract/types'

const TABS: { id: RightTab; label: string }[] = [
  { id: 'chat', label: 'สนทนา' },
  { id: 'watch', label: 'ติดตาม' },
  { id: 'tasks', label: 'งาน' },
  { id: 'memory', label: 'ความจำ' },
]

const selectClass =
  'rounded-lg border bg-[var(--color-surface)] px-2 py-1.5 text-[12px] text-[var(--color-cream)] outline-none'

function authBool(record: Record<string, unknown> | undefined, key: string): boolean {
  return Boolean(record?.[key])
}

function authText(record: Record<string, unknown> | undefined, key: string): string {
  const value = record?.[key]
  return typeof value === 'string' ? value : ''
}

function providerConfigured(
  reference: ProviderAuthReferenceResponse | null,
  providerId: AiProviderId,
): boolean | null {
  const provider = reference?.providers.find((item) => item.id === providerId)
  return typeof provider?.configured === 'boolean' ? provider.configured : null
}

function mergeProviderAuthStatus(
  current: ProviderAuthStatusResponse | null,
  next: ProviderAuthStatusResponse,
): ProviderAuthStatusResponse {
  const nextClaudeReady = next.claudeCode?.ready
  const currentClaudeReady = current?.claudeCode?.ready
  if (typeof nextClaudeReady !== 'boolean' && typeof currentClaudeReady === 'boolean' && current?.claudeCode) {
    return { ...next, claudeCode: current.claudeCode }
  }
  return next
}

export function RightDock() {
  const selectedDeptId = useUI((s) => s.selectedDeptId)
  const rightTab = useUI((s) => s.rightTab)
  const setRightTab = useUI((s) => s.setRightTab)
  const openEditDept = useUI((s) => s.openEditDept)
  const dockCollapsed = useUI((s) => s.dockCollapsed)
  const toggleDock = useUI((s) => s.toggleDock)
  const isDesktop = useIsDesktop()
  const speedCatalog = useSpeedCatalog()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [providerAuth, setProviderAuth] = useState<ProviderAuthStatusResponse | null>(null)
  const [providerReference, setProviderReference] = useState<ProviderAuthReferenceResponse | null>(null)
  const [authBusy, setAuthBusy] = useState<'chatgpt' | 'claude' | 'chatgptLogout' | 'claudeLogout' | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)

  const targetId = selectedDeptId ?? EXEC_ID
  // Select pure slices of company state, then resolve the targetId-dependent
  // values during render. useSelector memoizes on the company-state reference,
  // so a selector that closes over targetId would otherwise return a stale dept
  // until the next state tick — i.e. clicking a department would feel dead while
  // the engine is idle.
  const departments = useSelector((s) => s.departments, shallowArrayEqual)

  useEffect(() => {
    if (!settingsOpen) return
    let cancelled = false
    const load = async (probeClaude = false) => {
      try {
        const [status, reference] = await Promise.all([
          client.getProviderAuthStatus(probeClaude),
          client.getProviderAuthReference(),
        ])
        if (!cancelled) {
          setProviderAuth((current) => mergeProviderAuthStatus(current, status))
          setProviderReference(reference)
        }
      } catch (err) {
        if (!cancelled) setAuthError(err instanceof Error ? err.message : String(err))
      }
    }
    void load(true)
    const timer = window.setInterval(() => void load(false), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [settingsOpen])

  const refreshProviderAuth = async (probeClaude = false) => {
    const [status, reference] = await Promise.all([
      client.getProviderAuthStatus(probeClaude),
      client.getProviderAuthReference(),
    ])
    setProviderAuth((current) => mergeProviderAuthStatus(current, status))
    setProviderReference(reference)
    return status
  }

  const connectChatGPT = async () => {
    setAuthBusy('chatgpt')
    setAuthError(null)
    try {
      const session = await client.startChatGPTAccountLogin()
      const url = typeof session.authorizationUrl === 'string' ? session.authorizationUrl : ''
      if (url) {
        const opened = window.open(url, '_blank', 'noopener,noreferrer')
        if (!opened) setAuthError('browser blocked the ChatGPT login popup')
      }
      await refreshProviderAuth()
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthBusy(null)
    }
  }

  const connectClaudeCode = async () => {
    setAuthBusy('claude')
    setAuthError(null)
    try {
      const session = await client.startClaudeCodeLogin()
      if (session.status && typeof session.status === 'object') {
        setProviderAuth((current) => ({
          chatgptAccount: current?.chatgptAccount ?? {},
          claudeCode: session.status as Record<string, unknown>,
        }))
      }
      await refreshProviderAuth(true)
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthBusy(null)
    }
  }

  const disconnectChatGPT = async () => {
    if (!confirm('ออกจากบัญชี ChatGPT Account ใน ATRIUM?')) return
    setAuthBusy('chatgptLogout')
    setAuthError(null)
    try {
      const result = await client.disconnectChatGPTAccount()
      await refreshProviderAuth()
      const warning = typeof result.warning === 'string' ? result.warning : ''
      if (warning) setAuthError(warning)
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthBusy(null)
    }
  }

  const disconnectClaudeCode = async () => {
    if (!confirm('ออกจากบัญชี Claude Code Account บนเครื่องนี้?')) return
    setAuthBusy('claudeLogout')
    setAuthError(null)
    try {
      await client.disconnectClaudeCode()
      await refreshProviderAuth(true)
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err))
    } finally {
      setAuthBusy(null)
    }
  }

  const targetIsExec = isExec(targetId)
  useEffect(() => {
    if (!targetIsExec && rightTab === 'watch') setRightTab('chat')
  }, [rightTab, setRightTab, targetIsExec])

  if (isDesktop && dockCollapsed) {
    return <PanelStrip label="สนทนา" dir="open-left" onExpand={() => toggleDock(false)} />
  }

  const dept =
    departments.find((d) => d.id === targetId) ??
    departments.find((d) => d.id === EXEC_ID) ??
    null

  if (!dept) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-[var(--color-cream-faint)]">
        ไม่พบแผนก
      </div>
    )
  }

  const accentHex = ACCENT_HEX[dept.accent]
  const exec = isExec(dept.id)
  const tabs = exec ? TABS : TABS.filter((tab) => tab.id !== 'watch')
  const provider = AI_PROVIDERS[dept.providerId]
  const model = MODELS[dept.model]
  const thinkingEffort = THINKING_EFFORTS[dept.thinkingEffort]
  const providerModels = modelsForProvider(dept.providerId)
  const thinkingEfforts = thinkingEffortsForModel(dept.model)
  const routeLabel = providerRouteLabel(dept.providerId)
  const routeColor = dept.providerId === 'openai' || dept.providerId === 'chatgpt_account'
    ? ACCENT_HEX.sky
    : dept.providerId === 'claude_code'
      ? ACCENT_HEX.lavender
      : ACCENT_HEX.teal
  const fastSupported = speedCatalog.supportsFast(dept.model)
  const fastOn = dept.speed === 'fast'
  const chatgptAuth = providerAuth?.chatgptAccount
  const chatgptLogin = chatgptAuth?.login as Record<string, unknown> | null | undefined
  const chatgptReady = authBool(chatgptAuth, 'ready')
  const chatgptPending = authText(chatgptLogin ?? undefined, 'status') === 'pending'
  const chatgptBusy = authBusy === 'chatgpt' || authBusy === 'chatgptLogout'
  const claudeAuth = providerAuth?.claudeCode
  const claudeReady = authBool(claudeAuth, 'ready')
  const claudeBusy = authBusy === 'claude' || authBusy === 'claudeLogout'
  const claudeStale = authBool(claudeAuth, 'stale')
  const claudeState = authText(claudeAuth, 'state')
  const claudeProbeStatus = authText(claudeAuth, 'probeStatus')
  const claudeBaseStatus = authText(claudeAuth, 'subscriptionType') || authText(claudeAuth, 'authMethod') || authText(claudeAuth, 'status')
  const claudeStatus = claudeStale
    ? `last known ${claudeReady ? 'connected' : 'not connected'} · ${claudeProbeStatus || 'probe failed'}`
    : claudeState === 'unknown'
      ? `unknown · ${claudeProbeStatus || claudeBaseStatus || 'probe failed'}`
      : claudeBaseStatus
  const placeholder = exec
    ? `สั่งงาน ${dept.agentName} (ผู้บริหาร)…`
    : `คุยกับ ${dept.agentName} ฝ่าย${dept.name}…`

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* ---- compact context header ---- */}
      <div
        className="shrink-0 px-3.5 pt-2 pb-1.5"
        style={{
          borderBottom: '1px solid var(--color-line-soft)',
          background: `linear-gradient(165deg, ${withAlpha(accentHex, 0.1)}, transparent 72%)`,
        }}
      >
        <div className="flex items-center gap-2.5">
          <Avatar emoji={dept.emoji} accent={dept.accent} state={dept.state} size={36} />
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-[15px] leading-tight"
              style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
            >
              {exec ? dept.name : `ฝ่าย${dept.name}`}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-[var(--color-cream-dim)]">
              <Dot color={STATE_HEX[dept.state]} size={7} pulse={dept.state === 'working'} />
              <span style={{ color: STATE_HEX[dept.state] }}>{STATE_LABEL[dept.state]}</span>
              <span className="truncate text-[var(--color-cream-faint)]">· {dept.agentName} · {dept.role}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setSettingsOpen((v) => !v)}
            title="ตั้งค่าโมเดล / ฝ่าย"
            aria-pressed={settingsOpen}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border transition-colors"
            style={{
              borderColor: settingsOpen ? withAlpha(accentHex, 0.5) : 'var(--color-line-soft)',
              background: settingsOpen ? withAlpha(accentHex, 0.14) : 'transparent',
              color: settingsOpen ? accentHex : 'var(--color-cream-dim)',
            }}
          >
            <Icon name="model" size={16} />
          </button>
          {isDesktop && (
            <button
              type="button"
              onClick={() => toggleDock(true)}
              title="พับแผงแชท"
              aria-label="พับแผงแชท"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border text-[16px] leading-none text-[var(--color-cream-dim)] transition-colors hover:border-[var(--color-line)] hover:text-[var(--color-cream)]"
              style={{ borderColor: 'var(--color-line-soft)' }}
            >
              ›
            </button>
          )}
        </div>

        {/* glanceable model chip — tap to reveal full controls */}
        <button
          type="button"
          onClick={() => setSettingsOpen((v) => !v)}
          className="mt-1 flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] transition-colors hover:opacity-80"
          style={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-line-soft)',
            color: 'var(--color-cream-faint)',
          }}
        >
          <span style={{ color: withAlpha(accentHex, 0.95) }}>◆</span>
          <span className="truncate" style={{ fontFamily: 'var(--font-mono)' }}>{provider.shortLabel} · {model.label}</span>
          <span className="shrink-0" style={{ color: routeColor }}>· {routeLabel}</span>
          <span className="shrink-0">· คิด {thinkingEffort.label}</span>
          {fastOn && <span className="shrink-0" style={{ color: ACCENT_HEX.honey }}>· ⚡ Fast</span>}
          {dept.autonomy && <span className="shrink-0" style={{ color: ACCENT_HEX.teal }}>· ⌁ อัตโนมัติ</span>}
          <span className="shrink-0 opacity-60">{settingsOpen ? '▴' : '▾'}</span>
        </button>
      </div>

      {/* ---- collapsible settings ---- */}
      <AnimatePresence initial={false}>
        {settingsOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className="shrink-0 overflow-hidden"
            style={{ borderBottom: '1px solid var(--color-line-soft)', background: 'var(--color-surface)' }}
          >
            <div className="space-y-2.5 px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={dept.providerId}
                  onChange={(e) => client.setDepartmentProvider(dept.id, e.target.value as AiProviderId)}
                  className={selectClass}
                  style={{ borderColor: 'var(--color-line-soft)' }}
                  title="AI provider ของฝ่ายนี้"
                >
                  {PROVIDER_LIST.map((p) => {
                    const configured = providerConfigured(providerReference, p.id)
                    const readiness = configured === true ? 'พร้อม' : configured === false ? 'ยังไม่พร้อม' : 'ไม่ทราบสถานะ'
                    return (
                      <option key={p.id} value={p.id}>
                        {p.shortLabel} ({providerRouteLabel(p.id)}) · {readiness}
                      </option>
                    )
                  })}
                </select>

                <select
                  value={dept.model}
                  onChange={(e) => client.setDepartmentModel(dept.id, e.target.value as ModelId)}
                  className={selectClass}
                  style={{ borderColor: 'var(--color-line-soft)' }}
                  title="โมเดลของฝ่ายนี้"
                >
                  {providerModels.map((m) => (
                    <option key={m.id} value={m.id}>{m.label} · {modelRouteLabel(m.id)}</option>
                  ))}
                </select>

                <select
                  value={dept.thinkingEffort}
                  onChange={(e) => client.setDepartmentThinkingEffort(dept.id, e.target.value as ThinkingEffort)}
                  className={selectClass}
                  style={{ borderColor: 'var(--color-line-soft)' }}
                  title="ระดับ adaptive thinking ของฝ่ายนี้"
                >
                  {thinkingEfforts.map((effort) => (
                    <option key={effort.id} value={effort.id}>Think {effort.label}</option>
                  ))}
                </select>

                <button
                  type="button"
                  disabled={!fastSupported}
                  onClick={() => client.setDepartmentSpeed(dept.id, fastOn ? 'standard' : 'fast')}
                  className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  style={{
                    borderColor: fastOn ? withAlpha(ACCENT_HEX.honey, 0.5) : 'var(--color-line-soft)',
                    color: fastOn ? ACCENT_HEX.honey : 'var(--color-cream-faint)',
                  }}
                  title={
                    fastSupported
                      ? model.tier === 'gpt'
                        ? 'GPT Fast Mode — ใช้ reasoning effort ต่ำเพื่อลด latency'
                        : 'Claude Fast Mode — ตอบเร็วขึ้นสำหรับรุ่นที่รองรับ'
                      : model.tier === 'gpt'
                        ? 'GPT รุ่นนี้ยังไม่ประกาศรองรับ Fast Mode'
                        : 'รุ่นนี้ไม่รองรับ Fast Mode'
                  }
                  aria-pressed={fastOn}
                >
                  ⚡ Fast
                </button>

                <button
                  type="button"
                  onClick={() => client.setDepartmentAutonomy(dept.id, !dept.autonomy)}
                  className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
                  style={{
                    borderColor: dept.autonomy ? withAlpha(ACCENT_HEX.teal, 0.4) : 'var(--color-line-soft)',
                    color: dept.autonomy ? ACCENT_HEX.teal : 'var(--color-cream-faint)',
                  }}
                  title="ให้ฝ่ายริเริ่มงานเองได้"
                >
                  <Icon name="autonomy" size={13} /> ทำงานเอง
                </button>
              </div>

              <div
                className="grid gap-2 rounded-lg border px-2.5 py-2 text-[11px]"
                style={{
                  borderColor: 'var(--color-line-soft)',
                  background: 'rgba(19,16,9,0.34)',
                }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium text-[var(--color-cream)]">ChatGPT Account</div>
                    <div className="truncate text-[var(--color-cream-faint)]">
                      {chatgptReady
                        ? authText(chatgptAuth, 'email') || authText(chatgptAuth, 'chatgptPlanType') || 'connected'
                        : chatgptPending
                          ? 'waiting for browser login'
                          : 'not connected'}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={connectChatGPT}
                      disabled={chatgptBusy}
                      className="rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors disabled:opacity-50"
                      style={{
                        borderColor: chatgptReady ? withAlpha(ACCENT_HEX.teal, 0.45) : withAlpha(ACCENT_HEX.sky, 0.45),
                        color: chatgptReady ? ACCENT_HEX.teal : ACCENT_HEX.sky,
                      }}
                    >
                      {authBusy === 'chatgpt' ? 'กำลังเปิด…' : chatgptReady ? 'เชื่อมแล้ว' : chatgptPending ? 'เปิดอีกครั้ง' : 'เชื่อม'}
                    </button>
                    {(chatgptReady || chatgptPending) && (
                      <button
                        type="button"
                        onClick={disconnectChatGPT}
                        disabled={chatgptBusy}
                        className="rounded-lg border px-2 py-1.5 text-[12px] transition-colors disabled:opacity-50"
                        style={{
                          borderColor: withAlpha(STATE_HEX.blocked, 0.38),
                          color: STATE_HEX.blocked,
                        }}
                        title="ออกจากบัญชี ChatGPT Account เพื่อเปลี่ยนบัญชี"
                      >
                        {authBusy === 'chatgptLogout' ? 'กำลังออก…' : 'ออก'}
                      </button>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium text-[var(--color-cream)]">Claude Code Account</div>
                    <div className="truncate text-[var(--color-cream-faint)]">
                      {claudeReady ? claudeStatus || 'connected' : claudeStatus || 'not connected'}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={connectClaudeCode}
                      disabled={claudeBusy}
                      className="rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors disabled:opacity-50"
                      style={{
                        borderColor: claudeReady ? withAlpha(ACCENT_HEX.teal, 0.45) : withAlpha(ACCENT_HEX.lavender, 0.45),
                        color: claudeReady ? ACCENT_HEX.teal : ACCENT_HEX.lavender,
                      }}
                      title={claudeReady ? 'ตรวจสอบสถานะ Claude Code อีกครั้ง' : 'เปิด Claude Code login หรือตรวจสอบบัญชี'}
                    >
                      {authBusy === 'claude' ? 'กำลังตรวจ…' : claudeReady ? 'ตรวจอีกครั้ง' : 'เชื่อม'}
                    </button>
                    {claudeReady && (
                      <button
                        type="button"
                        onClick={disconnectClaudeCode}
                        disabled={claudeBusy}
                        className="rounded-lg border px-2 py-1.5 text-[12px] transition-colors disabled:opacity-50"
                        style={{
                          borderColor: withAlpha(STATE_HEX.blocked, 0.38),
                          color: STATE_HEX.blocked,
                        }}
                        title="ออกจากบัญชี Claude Code เพื่อเปลี่ยนบัญชี"
                      >
                        {authBusy === 'claudeLogout' ? 'กำลังออก…' : 'ออก'}
                      </button>
                    )}
                  </div>
                </div>

                {authError && <div className="text-[10px] text-[var(--color-coral)]">{authError}</div>}
              </div>

              {/* provider config (technical) */}
              <div
                className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 rounded-lg border px-2.5 py-2 text-[10px]"
                style={{
                  borderColor: 'var(--color-line-soft)',
                  background: 'rgba(19,16,9,0.3)',
                  color: 'var(--color-cream-faint)',
                }}
              >
                <span className="truncate">
                  {provider.label} · {routeLabel} · {provider.baseUrlEnv}={provider.baseUrl}
                </span>
                <span className="tabular-nums" style={{ fontFamily: 'var(--font-mono)' }}>{model.id}</span>
                <span className="truncate">
                  auth: {provider.authTokenEnv}
                  {provider.trafficEnv ? ` · ${provider.trafficEnv}=1` : ''}
                  {provider.wireApi ? ` · ${provider.wireApi}` : ''}
                </span>
                <span>{modelRouteLabel(model.id)} · Think {modelEffortLabel(model.id)}</span>
              </div>

              {/* dept actions */}
              {!exec && (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openEditDept(dept.id)}
                    className="rounded-lg border px-2.5 py-1.5 text-[12px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
                    style={{ borderColor: 'var(--color-line-soft)' }}
                    title="แก้ไขข้อมูลฝ่ายนี้"
                  >
                    แก้ไขฝ่าย
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm(`ปิดแผนก${dept.name}?`)) client.closeDepartment(dept.id)
                    }}
                    className="rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
                    style={{ borderColor: withAlpha(STATE_HEX.blocked, 0.4), color: STATE_HEX.blocked }}
                    title="ปิดแผนกนี้"
                  >
                    ปิดแผนก
                  </button>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ---- tab switcher ---- */}
      <div className="shrink-0 px-3.5 pt-1.5">
        <div
          className="flex gap-1 rounded-xl p-1"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
        >
          {tabs.map((t) => {
            const on = rightTab === t.id
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setRightTab(t.id)}
                className="relative flex-1 rounded-lg px-2 py-1 text-[13px] font-medium transition-colors"
                style={{ color: on ? '#1a1610' : 'var(--color-cream-dim)' }}
              >
                {on && (
                  <motion.span
                    layoutId="right-tab"
                    className="absolute inset-0 rounded-lg"
                    style={{ background: accentHex }}
                    transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                  />
                )}
                <span className="relative z-10">{t.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* ---- body ---- */}
      <div className="flex min-h-0 flex-1 flex-col px-3.5 pt-1.5 pb-2.5">
        {rightTab === 'chat' && (
          <ChatPanel
            key={threadIdFor(dept.id)}
            threadId={threadIdFor(dept.id)}
            accent={dept.accent}
            emoji={dept.emoji}
            placeholder={placeholder}
          />
        )}
        {rightTab === 'watch' && exec && <ExecutiveWorkMonitor />}
        {rightTab === 'tasks' && <DeptTasks deptId={exec ? null : dept.id} />}
        {rightTab === 'memory' && <MemoryViewer deptId={dept.id} accent={dept.accent} />}
      </div>
    </div>
  )
}
