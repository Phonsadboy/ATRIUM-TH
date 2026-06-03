import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { clockLabel, money } from '../../lib/format'
import { client } from '../../state/useCompany'
import { Markdown } from './Markdown'
import type {
  AccentName,
  ChatMessage,
  ChatToolRun,
  MessageCitation,
  MessageRenderMetadata,
  MessageRenderNotice,
  SuggestedFollowUp,
  TurnSegment,
  VideoJobRecord,
} from '../../contract/types'

/* ============================ Reasoning ============================ */

const REASONING_NOTE: Partial<Record<string, string>> = {
  redacted: 'เหตุผลถูกซ่อนโดยผู้ให้บริการ',
  omitted: 'ไม่ได้บันทึกสายความคิด',
}

/** Tiny animated dots shown while the model is actively thinking. */
function LiveDots({ color }: { color: string }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="inline-block h-1 w-1 rounded-full"
          style={{ background: color }}
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </span>
  )
}

export function ReasoningBlock({ m, accent }: { m: ChatMessage; accent: AccentName }) {
  const status = m.reasoningStatus ?? (m.reasoning ? 'available' : null)
  const hasText = !!(m.reasoning && m.reasoning.trim())
  const note = status ? REASONING_NOTE[status] : undefined
  const live = !!m.streaming && hasText
  if (!hasText && !note && !m.reasoningSummary) return null
  const tokens = m.thinkingTokens ? `${m.thinkingTokens.toLocaleString()} โทเคน` : null
  const hex = ACCENT_HEX[accent]
  return (
    <details className="group mt-1.5 min-w-0 max-w-full" open={live || undefined}>
      <summary
        className="flex w-full min-w-0 max-w-full cursor-pointer list-none items-center gap-1.5 overflow-hidden rounded-lg px-2 py-1 text-[12px] transition-colors hover:bg-[var(--color-surface)]"
        style={{ color: withAlpha(hex, 0.95) }}
      >
        <span className="shrink-0 transition-transform group-open:rotate-90">▸</span>
        <span className="shrink-0 font-medium">🧠 {live ? 'กำลังคิด' : 'สายความคิด'}</span>
        {live && <LiveDots color={hex} />}
        {m.reasoningSummary && !live && (
          <span className="min-w-0 flex-1 truncate font-normal text-[var(--color-cream-faint)]">· {m.reasoningSummary}</span>
        )}
        {tokens && <span className="ml-auto shrink-0 font-normal text-[var(--color-cream-faint)]">· {tokens}</span>}
      </summary>
      {hasText ? (
        <div
          className="mt-1.5 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-xl px-3 py-2.5 text-[13px] leading-[1.6]"
          style={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-line-soft)',
            borderLeft: `2px solid ${withAlpha(hex, 0.55)}`,
            color: 'var(--color-cream-dim)',
          }}
        >
          {m.reasoning}
        </div>
      ) : (
        <div className="mt-1 px-2 text-[12px] italic text-[var(--color-cream-faint)]">{note}</div>
      )}
    </details>
  )
}

/* ============================ Citations ============================ */

const CITE_EMOJI: Record<string, string> = {
  knowledge: '📎',
  archive: '🗄️',
  graph: '🕸️',
  memory: '🧩',
}

export function Citations({
  render,
  onOpen,
}: {
  render?: MessageRenderMetadata | null
  onOpen?: (c: MessageCitation) => void
}) {
  const cites = render?.citations ?? []
  if (!cites.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {cites.map((c) => (
        <button
          key={c.id}
          type="button"
          title={c.snippet ?? c.title ?? c.label}
          onClick={() => onOpen?.(c)}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-opacity hover:opacity-80"
          style={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-line-soft)',
            color: 'var(--color-cream-faint)',
          }}
        >
          <span>{CITE_EMOJI[c.kind ?? 'memory'] ?? '📎'}</span>
          <span className="max-w-[180px] truncate">{c.title || c.label}</span>
          {typeof c.score === 'number' && <span>· {Math.round(c.score * 100)}%</span>}
        </button>
      ))}
    </div>
  )
}

/* ========================== Follow-ups ============================ */

export function FollowUps({
  items,
  accent,
  onPick,
}: {
  items?: SuggestedFollowUp[]
  accent: AccentName
  onPick: (text: string) => void
}) {
  if (!items?.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {items.map((f) => (
        <button
          key={f.id}
          type="button"
          onClick={() => onPick(f.text)}
          className="rounded-full px-3 py-1 text-[12px] transition-opacity hover:opacity-80"
          style={{
            background: withAlpha(ACCENT_HEX[accent], 0.12),
            border: `1px solid ${withAlpha(ACCENT_HEX[accent], 0.3)}`,
            color: 'var(--color-cream)',
          }}
        >
          {f.label}
        </button>
      ))}
    </div>
  )
}

/* ========================== Reactions ============================ */

const QUICK = ['👍', '❤️', '🎯', '❌']

export function Reactions({
  m,
  onToggle,
}: {
  m: ChatMessage
  onToggle: (emoji: string, on: boolean) => void
}) {
  const reactions = m.reactions ?? []
  const counts = new Map<string, { n: number; mine: boolean }>()
  for (const r of reactions) {
    const prev = counts.get(r.emoji) ?? { n: 0, mine: false }
    counts.set(r.emoji, { n: prev.n + 1, mine: prev.mine || r.actor === 'owner' })
  }
  if (counts.size === 0) return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {[...counts.entries()].map(([emoji, { n, mine }]) => (
        <button
          key={emoji}
          type="button"
          onClick={() => onToggle(emoji, !mine)}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px]"
          style={{
            background: mine ? withAlpha('#57d6bf', 0.18) : 'var(--color-surface)',
            border: `1px solid ${mine ? withAlpha('#57d6bf', 0.4) : 'var(--color-line-soft)'}`,
          }}
        >
          <span>{emoji}</span>
          <span className="text-[var(--color-cream-faint)]">{n}</span>
        </button>
      ))}
    </div>
  )
}

export function QuickReactBar({ onReact }: { onReact: (emoji: string) => void }) {
  return (
    <div className="flex gap-0.5">
      {QUICK.map((e) => (
        <button
          key={e}
          type="button"
          onClick={() => onReact(e)}
          className="rounded-md px-1 text-[14px] transition-transform hover:scale-125"
        >
          {e}
        </button>
      ))}
    </div>
  )
}

/* ========================== Tool runs ============================ */

const TOOL_STATUS: Record<string, { c: string; label: string }> = {
  completed: { c: '#57d6bf', label: '✓' },
  succeeded: { c: '#57d6bf', label: '✓' },
  pending_approval: { c: '#f4a945', label: '⏳ รออนุมัติ' },
  failed: { c: '#f0735f', label: '✕ ล้มเหลว' },
  cancelled: { c: '#6f6657', label: '⊘ ยกเลิก' },
  running: { c: '#7eb0dc', label: '… กำลังทำ' },
}

/** Friendly Thai verb per chat tool so the live card reads as an action. */
const TOOL_LABEL: Record<string, string> = {
  create_department: 'สร้างฝ่าย',
  create_task: 'สร้างงาน',
  search_memory: 'ค้นความจำ',
  query_department: 'สอบถามฝ่าย',
  get_finance_snapshot: 'ดูสถานะการเงิน',
  schedule_meeting: 'นัดประชุม',
  create_artifact: 'สร้างเอกสาร',
  generate_image_asset: 'สร้างภาพ',
  escalate_to_owner: 'ส่งเรื่องถึงเจ้าของ',
  run_owner_tool: 'ใช้เครื่องเจ้าของ',
  call_atrium_api: 'เรียกฟังก์ชัน ATRIUM',
}

/** Pull the most meaningful argument out of a tool call to show inline. */
function toolRunHint(run: ChatToolRun): string | null {
  const a = (run.args ?? {}) as Record<string, unknown>
  const pick = (...keys: string[]): string | null => {
    for (const k of keys) {
      const v = a[k]
      if (typeof v === 'string' && v.trim()) return v
    }
    return null
  }
  const hint =
    pick('path', 'tool', 'title', 'name', 'query', 'q', 'departmentId', 'detail', 'reason') ??
    (() => {
      const first = Object.values(a).find((v) => typeof v === 'string' && v.trim())
      return typeof first === 'string' ? first : null
    })()
  if (!hint) return null
  return hint.length > 60 ? `${hint.slice(0, 57)}…` : hint
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function stringValue(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function nestedRecord(source: Record<string, unknown> | null, key: string): Record<string, unknown> | null {
  return asRecord(source?.[key])
}

type VideoJobSummary = {
  jobId: string
  tool: string
  status: string
  projectId: string
  statusUrl: string
  manifestPath: string
  logPath: string
  phase: string
  message: string
  percent: number | null
  artifactId: string
  renderId: string
  motionId: string
  transcriptId: string
  renderers: string[]
  error: string
}

const VIDEO_JOB_TERMINAL_STATUSES = new Set(['done', 'succeeded', 'completed', 'failed', 'cancelled'])

function videoJobSummary(run: ChatToolRun): VideoJobSummary | null {
  const result = asRecord(run.result)
  const args = asRecord(run.args)
  const ownerRun = nestedRecord(result, 'run')
  const ownerResult = nestedRecord(ownerRun, 'result') ?? nestedRecord(result, 'runResult') ?? result
  const job = nestedRecord(ownerResult, 'job') ?? nestedRecord(result, 'job')
  const ownerTool = stringValue(ownerRun?.tool) || stringValue(args?.tool) || run.tool
  const jobId = stringValue(job?.id) || stringValue(job?.jobId) || stringValue(ownerResult?.jobId) || stringValue(result?.jobId)
  const isVideoJob =
    ownerTool.startsWith('video.') &&
    (Boolean(jobId) || Boolean(job) || ownerResult?.background === true || ownerTool === 'video.job_status')
  if (!isVideoJob) return null
  const progress = nestedRecord(job, 'progress')
  const jobResult = nestedRecord(job, 'result') ?? ownerResult
  const artifact = nestedRecord(jobResult, 'artifact') ?? nestedRecord(jobResult, 'renderArtifact')
  const render = nestedRecord(jobResult, 'render')
  const motion = nestedRecord(jobResult, 'motion')
  const transcript = nestedRecord(jobResult, 'transcript')
  const rawRenderers = motion?.renderers
  const renderers = Array.isArray(rawRenderers)
    ? rawRenderers.map((item) => stringValue(item)).filter(Boolean)
    : [stringValue(motion?.renderer)].filter(Boolean)
  return {
    jobId,
    tool: stringValue(job?.tool) || ownerTool,
    status: stringValue(job?.status) || stringValue(ownerResult?.status) || stringValue(ownerRun?.status) || run.status,
    projectId: stringValue(job?.projectId) || stringValue(motion?.projectId) || stringValue(render?.projectId),
    statusUrl: stringValue(job?.statusUrl) || stringValue(ownerResult?.statusUrl) || stringValue(result?.statusUrl),
    manifestPath: stringValue(job?.manifestPath),
    logPath: stringValue(job?.logPath),
    phase: stringValue(progress?.phase),
    message: stringValue(progress?.message),
    percent: numberValue(progress?.percent),
    artifactId: stringValue(artifact?.id) || stringValue(render?.artifactId) || stringValue(motion?.artifactId),
    renderId: stringValue(render?.id) || stringValue(render?.renderId),
    motionId: stringValue(motion?.id) || stringValue(motion?.motionId),
    transcriptId: stringValue(transcript?.id) || stringValue(transcript?.transcriptId),
    renderers,
    error: stringValue(job?.error) || stringValue(ownerRun?.error) || stringValue(run.error),
  }
}

function videoJobSummaryFromRecord(base: VideoJobSummary, record: VideoJobRecord): VideoJobSummary {
  const job = asRecord(record)
  const progress = nestedRecord(job, 'progress')
  const jobResult = nestedRecord(job, 'result')
  const artifact = nestedRecord(jobResult, 'artifact') ?? nestedRecord(jobResult, 'renderArtifact')
  const render = nestedRecord(jobResult, 'render')
  const motion = nestedRecord(jobResult, 'motion')
  const transcript = nestedRecord(jobResult, 'transcript')
  const rawRenderers = motion?.renderers
  const renderers = Array.isArray(rawRenderers)
    ? rawRenderers.map((item) => stringValue(item)).filter(Boolean)
    : [stringValue(motion?.renderer)].filter(Boolean)
  return {
    jobId: stringValue(job?.id) || stringValue(job?.jobId) || base.jobId,
    tool: stringValue(job?.tool) || base.tool,
    status: stringValue(job?.status) || base.status,
    projectId: stringValue(job?.projectId) || stringValue(motion?.projectId) || stringValue(render?.projectId) || base.projectId,
    statusUrl: stringValue(job?.statusUrl) || base.statusUrl,
    manifestPath: stringValue(job?.manifestPath) || base.manifestPath,
    logPath: stringValue(job?.logPath) || base.logPath,
    phase: stringValue(progress?.phase) || base.phase,
    message: stringValue(progress?.message) || base.message,
    percent: numberValue(progress?.percent) ?? base.percent,
    artifactId: stringValue(artifact?.id) || stringValue(render?.artifactId) || stringValue(motion?.artifactId) || base.artifactId,
    renderId: stringValue(render?.id) || stringValue(render?.renderId) || base.renderId,
    motionId: stringValue(motion?.id) || stringValue(motion?.motionId) || base.motionId,
    transcriptId: stringValue(transcript?.id) || stringValue(transcript?.transcriptId) || base.transcriptId,
    renderers: renderers.length ? renderers : base.renderers,
    error: stringValue(job?.error) || base.error,
  }
}

function isVideoJobTerminal(status: string): boolean {
  return VIDEO_JOB_TERMINAL_STATUSES.has(status.toLowerCase())
}

function videoJobStatusColor(status: string): string {
  if (status === 'done' || status === 'succeeded' || status === 'completed') return ACCENT_HEX.teal
  if (status === 'failed') return ACCENT_HEX.coral
  if (status === 'cancelled') return '#6f6657'
  if (status === 'queued') return ACCENT_HEX.amber
  return ACCENT_HEX.sky
}

function compactId(value: string): string {
  if (!value) return ''
  return value.length > 28 ? `${value.slice(0, 14)}…${value.slice(-8)}` : value
}

function VideoJobCard({ job }: { job: VideoJobSummary }) {
  const [latest, setLatest] = useState<VideoJobRecord | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState('')
  const displayed = useMemo(() => (latest ? videoJobSummaryFromRecord(job, latest) : job), [job, latest])
  const color = videoJobStatusColor(displayed.status)
  const active = Boolean(displayed.jobId && !isVideoJobTerminal(displayed.status))

  useEffect(() => {
    setLatest(null)
    setRefreshError('')
  }, [job.jobId])

  useEffect(() => {
    if (!job.jobId || isVideoJobTerminal(displayed.status)) return undefined
    let alive = true
    const loadJob = async () => {
      try {
        const record = await client.getVideoJob(job.jobId)
        if (!alive) return
        setLatest(record)
        setRefreshError('')
      } catch (error) {
        if (!alive) return
        setRefreshError(error instanceof Error ? error.message : 'อัปเดตสถานะไม่ได้')
      }
    }
    void loadJob()
    const timer = window.setInterval(() => void loadJob(), 2500)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [job.jobId, displayed.status])

  const refreshNow = async () => {
    if (!displayed.jobId || refreshing) return
    setRefreshing(true)
    try {
      const record = await client.getVideoJob(displayed.jobId)
      setLatest(record)
      setRefreshError('')
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : 'อัปเดตสถานะไม่ได้')
    } finally {
      setRefreshing(false)
    }
  }

  const rows = [
    ['job', displayed.jobId],
    ['project', displayed.projectId],
    ['artifact', displayed.artifactId],
    ['render', displayed.renderId],
    ['motion', displayed.motionId],
    ['transcript', displayed.transcriptId],
    ['statusUrl', displayed.statusUrl],
    ['log', displayed.logPath],
  ].filter(([, value]) => value)
  return (
    <div
      className="mx-3 mb-2 rounded-lg px-2.5 py-2 text-[12px]"
      style={{ background: withAlpha(color, 0.08), border: `1px solid ${withAlpha(color, 0.28)}` }}
      data-testid="video-job-status-card"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold" style={{ color, background: withAlpha(color, 0.14) }}>
          video job
        </span>
        <span className="min-w-0 flex-1 truncate text-[var(--color-cream-dim)]">
          {displayed.tool}{displayed.renderers.length ? ` · ${displayed.renderers.join('+')}` : ''}
        </span>
        {active && <LiveDots color={color} />}
        <button
          type="button"
          onClick={() => void refreshNow()}
          disabled={!displayed.jobId || refreshing}
          className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] transition-opacity hover:opacity-80 disabled:opacity-40"
          style={{ color, border: `1px solid ${withAlpha(color, 0.28)}` }}
          data-testid="video-job-refresh"
        >
          {refreshing ? 'กำลังรีเฟรช' : 'รีเฟรช'}
        </button>
        <span className="shrink-0 font-medium" style={{ color }}>{displayed.status || '-'}</span>
      </div>
      {(displayed.phase || displayed.message || displayed.percent !== null) && (
        <div className="mt-2" data-testid="video-job-progress">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-[var(--color-cream-faint)]">
            <span className="shrink-0">{displayed.phase || 'progress'}</span>
            {displayed.message && <span className="min-w-0 flex-1 truncate">{displayed.message}</span>}
            {displayed.percent !== null && <span className="shrink-0">{displayed.percent}%</span>}
          </div>
          {displayed.percent !== null && (
            <div className="h-1.5 overflow-hidden rounded-full bg-black/20">
              <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(displayed.percent, 100))}%`, background: color }} />
            </div>
          )}
        </div>
      )}
      {rows.length > 0 && (
        <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px]" data-testid="video-job-handles">
          {rows.slice(0, 6).map(([label, value]) => (
            <div key={label} className="contents">
              <span className="text-[var(--color-cream-faint)]">{label}</span>
              <span className="min-w-0 truncate text-[var(--color-cream-dim)]" title={value}>{compactId(value)}</span>
            </div>
          ))}
        </div>
      )}
      {displayed.artifactId && <div className="sr-only" data-testid="video-job-result-artifact">{displayed.artifactId}</div>}
      {displayed.error && <div className="mt-1.5 text-[11px]" style={{ color: ACCENT_HEX.coral }}>{displayed.error}</div>}
      {refreshError && <div className="mt-1.5 text-[11px] text-[var(--color-cream-faint)]">{refreshError}</div>}
    </div>
  )
}

function ToolRunCard({ run }: { run: ChatToolRun }) {
  const [open, setOpen] = useState(false)
  const st = TOOL_STATUS[run.status] ?? { c: '#aba090', label: run.status }
  const title = run.label || run.summary || TOOL_LABEL[run.tool] || run.tool
  const hint = toolRunHint(run)
  const running = run.status === 'running'
  const hasDetail = !!(run.args && Object.keys(run.args).length) || !!run.result || !!run.error
  const videoJob = videoJobSummary(run)
  return (
    <div
      className="rounded-xl text-[13px]"
      style={{ background: 'var(--color-surface)', border: `1px solid ${withAlpha(st.c, 0.4)}` }}
    >
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span>🔧</span>
        <span className="flex-1 truncate" style={{ color: 'var(--color-cream)' }}>
          <span className="font-medium">{title}</span>
          {hint && <span className="text-[var(--color-cream-faint)]"> · {hint}</span>}
        </span>
        {run.argsRedacted && <span title="ข้อมูลถูกปกปิด">🔒</span>}
        <span className="shrink-0 inline-flex items-center gap-1" style={{ color: st.c }}>
          {running && <LiveDots color={st.c} />}
          {st.label}
        </span>
        {hasDetail && <span className="shrink-0 text-[var(--color-cream-faint)]">{open ? '▾' : '▸'}</span>}
      </button>
      {videoJob && <VideoJobCard job={videoJob} />}
      {open && hasDetail && (
        <div className="space-y-1 px-3 pb-2.5 text-[12px]" style={{ color: 'var(--color-cream-faint)' }}>
          {run.args && Object.keys(run.args).length > 0 && (
            <pre className="overflow-x-auto whitespace-pre-wrap">{JSON.stringify(run.args, null, 2)}</pre>
          )}
          {run.error && <div style={{ color: '#f0735f' }}>error: {run.error}</div>}
          {run.result != null && (
            <pre className="overflow-x-auto whitespace-pre-wrap">
              {typeof run.result === 'string' ? run.result : JSON.stringify(run.result, null, 2)}
            </pre>
          )}
          {run.policyReason && <div>policy: {run.policyReason}</div>}
        </div>
      )}
    </div>
  )
}

/** Roll every run's state up into one badge for the collapsed header. */
function toolRunIdentity(run: ChatToolRun): string {
  const args = (run.args ?? {}) as Record<string, unknown>
  const nestedTool = typeof args.tool === 'string' ? args.tool : ''
  return `${run.tool}:${nestedTool}`
}

function toolRunsBadge(runs: ChatToolRun[]): { c: string; label: string; spin: boolean } {
  if (runs.some((r) => r.status === 'running')) return { c: '#7eb0dc', label: 'กำลังทำ', spin: true }
  if (runs.some((r) => r.status === 'pending_approval')) return { c: '#f4a945', label: 'รออนุมัติ', spin: false }
  const failedRuns = runs.filter((r) => r.status === 'failed')
  if (failedRuns.length) {
    const failedKeys = new Set(failedRuns.map(toolRunIdentity))
    const recovered = runs.some((r) => r.status === 'succeeded' && failedKeys.has(toolRunIdentity(r)))
    if (recovered) return { c: ACCENT_HEX.honey, label: 'retry สำเร็จ', spin: false }
    return { c: '#f0735f', label: 'บางรายการล้มเหลว', spin: false }
  }
  return { c: '#57d6bf', label: 'เสร็จแล้ว', spin: false }
}

export function ToolRuns({ runs, streaming }: { runs?: ChatToolRun[] | null; streaming?: boolean }) {
  if (!runs?.length) return null
  const badge = toolRunsBadge(runs)
  // Expand only while the turn is live (streaming) or a run still needs
  // attention; once everything settles it collapses to a one-line summary.
  const active = !!streaming || badge.spin || runs.some((r) => r.status === 'pending_approval')
  const names = runs.map((r) => r.label || r.summary || TOOL_LABEL[r.tool] || r.tool)
  const preview = names.slice(0, 3).join(' · ') + (names.length > 3 ? ` +${names.length - 3}` : '')
  return (
    <details className="group mt-2 min-w-0 max-w-full" open={active || undefined}>
      <summary className="flex w-full min-w-0 max-w-full cursor-pointer list-none items-center gap-1.5 overflow-hidden rounded-lg px-2 py-1 text-[12px] transition-colors hover:bg-[var(--color-surface)]">
        <span className="shrink-0 transition-transform group-open:rotate-90">▸</span>
        <span className="shrink-0">🔧</span>
        <span className="shrink-0 font-medium text-[var(--color-cream-dim)]">เครื่องมือ {runs.length} รายการ</span>
        <span className="min-w-0 flex-1 truncate font-normal text-[var(--color-cream-faint)] group-open:opacity-0">· {preview}</span>
        <span className="ml-auto shrink-0 inline-flex items-center gap-1" style={{ color: badge.c }}>
          {badge.spin && <LiveDots color={badge.c} />}
          {badge.label}
        </span>
      </summary>
      <div className="mt-1.5 space-y-1.5">
        {runs.map((r) => <ToolRunCard key={r.id} run={r} />)}
      </div>
    </details>
  )
}

/* ========================== Turn activity ========================== */

/** Groups one reply's "process" (thinking + tool calls) into a single rail that
 *  sits *above* the final answer, so it reads as one round and the answer text
 *  stays at the bottom. Renders nothing when there's no process to show. */
export function TurnActivity({ m, accent }: { m: ChatMessage; accent: AccentName }) {
  const status = m.reasoningStatus ?? (m.reasoning ? 'available' : null)
  const hasReasoning =
    !!(m.reasoning && m.reasoning.trim()) || !!m.reasoningSummary || !!(status && REASONING_NOTE[status])
  const hasTools = !!m.toolRuns?.length
  if (!hasReasoning && !hasTools) return null
  return (
    <div
      className="mb-1.5 mt-1 min-w-0 max-w-full rounded-r-md pl-2.5"
      style={{ borderLeft: `2px solid ${withAlpha(ACCENT_HEX[accent], 0.3)}` }}
    >
      <ReasoningBlock m={m} accent={accent} />
      <ToolRuns runs={m.toolRuns} streaming={!!m.streaming} />
    </div>
  )
}

/* ========================== Turn timeline ========================== */

/** First meaningful line of a thinking block (its bold "**title**"), used as the
 *  collapsed-state hint. */
function thinkingTitle(text: string): string {
  const first = text.split('\n').map((l) => l.trim()).find(Boolean) ?? ''
  const m = /^\*{1,2}(.+?)\*{1,2}$/.exec(first)
  const title = (m ? m[1] : first).trim()
  return title.length > 80 ? `${title.slice(0, 77)}…` : title
}

/** One thinking step in the timeline — same look as ReasoningBlock but for a
 *  single segment's text. Auto-opens only while it is the live (active) step. */
function ThinkingSegment({ text, accent, live }: { text: string; accent: AccentName; live?: boolean }) {
  const body = text.trim()
  if (!body) return null
  const hex = ACCENT_HEX[accent]
  const title = thinkingTitle(body)
  return (
    <details className="group mt-1.5 min-w-0 max-w-full" open={live || undefined}>
      <summary
        className="flex w-full min-w-0 max-w-full cursor-pointer list-none items-center gap-1.5 overflow-hidden rounded-lg px-2 py-1 text-[12px] transition-colors hover:bg-[var(--color-surface)]"
        style={{ color: withAlpha(hex, 0.95) }}
      >
        <span className="shrink-0 transition-transform group-open:rotate-90">▸</span>
        <span className="shrink-0 font-medium">🧠 {live ? 'กำลังคิด' : 'สายความคิด'}</span>
        {live && <LiveDots color={hex} />}
        {!live && title && (
          <span className="min-w-0 flex-1 truncate font-normal text-[var(--color-cream-faint)]">· {title}</span>
        )}
      </summary>
      <div
        className="mt-1.5 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-xl px-3 py-2.5 text-[13px] leading-[1.6]"
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-line-soft)',
          borderLeft: `2px solid ${withAlpha(hex, 0.55)}`,
          color: 'var(--color-cream-dim)',
        }}
      >
        {body}
      </div>
    </details>
  )
}

/** Assistant prose produced mid-turn (before the final answer) — shown in the
 *  rail at the point it happened so the chronology stays intact. */
function IntermediateText({ text }: { text: string }) {
  const body = text.trim()
  if (!body) return null
  return (
    <div className="mt-1.5 px-1 text-[var(--color-cream-dim)]">
      <Markdown text={body} />
    </div>
  )
}

/** Renders one assistant turn as an ordered rail: think → use tools → think
 *  again → … The final answer is NOT here (it renders below the rail). */
export function TurnTimeline({
  segments,
  accent,
  tailActive,
}: {
  segments: TurnSegment[]
  accent: AccentName
  tailActive?: boolean
}) {
  if (!segments.length) return null
  const lastIdx = segments.length - 1
  return (
    <div
      className="mb-1.5 mt-1 min-w-0 max-w-full rounded-r-md pl-2.5"
      style={{ borderLeft: `2px solid ${withAlpha(ACCENT_HEX[accent], 0.3)}` }}
    >
      {segments.map((seg, i) => {
        const live = !!tailActive && i === lastIdx
        if (seg.kind === 'thinking') return <ThinkingSegment key={i} text={seg.text} accent={accent} live={live} />
        if (seg.kind === 'tools') return <ToolRuns key={i} runs={seg.runs} streaming={live} />
        return <IntermediateText key={i} text={seg.text} />
      })}
    </div>
  )
}

/* ============================ Notices ============================ */

const NOTICE: Record<MessageRenderNotice, { c: string; emoji: string; label: string }> = {
  budget_guardrail: { c: '#f0735f', emoji: '⛔', label: 'ติด budget guardrail — หยุดเรียกโมเดล' },
  context_compacted: { c: '#7eb0dc', emoji: '🗜️', label: 'ย่อบริบทบทสนทนาแล้ว' },
  rate_limited: { c: '#f4a945', emoji: '⏳', label: 'ส่งข้อความถี่เกินไป' },
  rate_limit_warning: { c: '#f4a945', emoji: '⚠️', label: 'ใกล้ลิมิตการส่งข้อความ' },
  budget_warning: { c: '#f4a945', emoji: '💸', label: 'ใกล้ถึงเพดานงบประมาณวันนี้' },
  approval_required: { c: '#b3a4ee', emoji: '🔐', label: 'ต้องอนุมัติก่อนดำเนินการ' },
  message_failed: { c: '#f0735f', emoji: '❌', label: 'ส่งข้อความไม่สำเร็จ' },
  runtime_dependency: { c: '#f4a945', emoji: '⚙️', label: 'runtime dependency ยังไม่พร้อม' },
}

export function NoticeCards({
  m,
  onResolveApproval,
}: {
  m: ChatMessage
  onResolveApproval?: (approvalId: string, decision: 'approved' | 'rejected') => void
}) {
  const notices = (m.render?.notices ?? []).filter(
    (n) => n !== 'approval_required' || (m.approvalId && m.approvalStatus === 'pending' && m.status === 'pending_approval'),
  )
  if (!notices.length) return null
  return (
    <div className="mt-2 space-y-1.5">
      {notices.map((n) => {
        const spec = NOTICE[n as MessageRenderNotice]
        if (!spec) return null
        const showApproval = n === 'approval_required' && m.approvalId && m.approvalStatus === 'pending'
        return (
          <div
            key={n}
            className="rounded-xl px-3 py-2 text-[13px]"
            style={{ background: withAlpha(spec.c, 0.1), border: `1px solid ${withAlpha(spec.c, 0.4)}` }}
          >
            <div className="flex items-center gap-1.5" style={{ color: spec.c }}>
              <span>{spec.emoji}</span>
              <span className="font-medium">{spec.label}</span>
            </div>
            {showApproval && onResolveApproval && (
              <div className="mt-2 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => onResolveApproval(m.approvalId!, 'approved')}
                  className="rounded-lg px-2.5 py-1 text-[12px] font-semibold"
                  style={{ background: withAlpha('#57d6bf', 0.2), color: '#57d6bf' }}
                >
                  ✓ อนุมัติ
                </button>
                <button
                  type="button"
                  onClick={() => onResolveApproval(m.approvalId!, 'rejected')}
                  className="rounded-lg px-2.5 py-1 text-[12px] font-semibold"
                  style={{ background: withAlpha('#f0735f', 0.2), color: '#f0735f' }}
                >
                  ✕ ปฏิเสธ
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ============================ Flow card ============================ */

function sameFlowText(a?: string | null, b?: string | null): boolean {
  const x = (a ?? '').replace(/\s+/g, ' ').trim()
  const y = (b ?? '').replace(/\s+/g, ' ').trim()
  return !!x && x === y
}

export function FlowCard({ m }: { m: ChatMessage }) {
  const flow = m.flow
  if (!flow?.steps?.length) return null
  const firstStep = flow.steps[0]
  const firstStepLabel = firstStep?.label ?? firstStep?.kind ?? null
  if (
    flow.kind === 'activity' &&
    flow.steps.length === 1 &&
    (sameFlowText(flow.title, m.text) || sameFlowText(firstStepLabel, m.text) || sameFlowText(flow.title, firstStepLabel))
  ) {
    return null
  }
  return (
    <div
      className="mt-2 rounded-xl px-3 py-2 text-[13px]"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
    >
      {flow.title && <div className="mb-1.5 font-medium" style={{ color: 'var(--color-cream)' }}>{flow.title}</div>}
      <div className="flex flex-wrap items-center gap-1 text-[var(--color-cream-faint)]">
        {flow.steps.map((s, i) => (
          <span key={i} className="inline-flex items-center gap-1">
            {i > 0 && <span style={{ color: '#b3a4ee' }}>→</span>}
            <span
              className="rounded-md px-1.5 py-0.5"
              style={{ background: withAlpha('#b3a4ee', 0.12) }}
            >
              {s.label || s.kind}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

/* ============================ Meta footer ============================ */

export function MetaFooter({ m }: { m: ChatMessage }) {
  const cost = m.render?.costUsd
  const bits: string[] = []
  if (m.generationMs) bits.push(`${(m.generationMs / 1000).toFixed(1)} วิ`)
  if (m.render?.format === 'markdown' && m.thinkingTokens) bits.push(`${m.thinkingTokens.toLocaleString()} think`)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-[var(--color-cream-faint)]">
      <span>{clockLabel(m.ts)}</span>
      {typeof cost === 'number' && cost > 0 && <span title="ต้นทุนข้อความนี้">· {money(cost)}</span>}
      {bits.map((b) => <span key={b}>· {b}</span>)}
    </div>
  )
}

/* ============================ Quote preview ============================ */

export function QuotePreview({ m }: { m: ChatMessage }) {
  if (!m.quoteText) return null
  return (
    <div
      className="mb-1.5 border-l-2 pl-2 text-[12px] text-[var(--color-cream-faint)]"
      style={{ borderColor: withAlpha('#aba090', 0.5) }}
    >
      {m.quoteAuthorName && <span className="font-medium">{m.quoteAuthorName}: </span>}
      <span className="line-clamp-2">{m.quoteText}</span>
    </div>
  )
}

/* ============================ Typing dots ============================ */

export function TypingDots({ color }: { color: string }) {
  return (
    <span className="inline-flex items-center gap-1 py-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: color }}
          animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </span>
  )
}
