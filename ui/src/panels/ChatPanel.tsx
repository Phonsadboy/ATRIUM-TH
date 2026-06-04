import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Avatar, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { money } from '../lib/format'
import type {
  AccentName,
  Artifact,
  ChatMessage,
  MessageAttachment,
  MessageCitation,
  PromptStarter,
  ThinkingEffort,
  ThreadCostSummary,
  TurnSegment,
} from '../contract/types'
import { Markdown } from './chat/Markdown'
import { Composer } from './chat/Composer'
import {
  Citations,
  FlowCard,
  FollowUps,
  MetaFooter,
  NoticeCards,
  QuickReactBar,
  QuotePreview,
  Reactions,
  TurnActivity,
  TurnTimeline,
  TypingDots,
} from './chat/chatParts'

const EMPTY: ChatMessage[] = []
const HISTORY_PAGE_SIZE = 30

type ThreadHistoryState = {
  threadId: string
  messages: ChatMessage[]
  loadingInitial: boolean
  loadingOlder: boolean
  loadingNewer: boolean
  hasMoreBefore: boolean
  error: string | null
}

/** Split a turn's segments into the rail (everything that led up to the answer,
 *  in chronological order) and the final answer text (rendered at the very
 *  bottom). `rail: null` ⇒ no live-built timeline → fall back to `TurnActivity`. */
function splitTurnSegments(m: ChatMessage): { rail: TurnSegment[] | null; answer: string } {
  const segs = m.segments
  if (!segs || !segs.length) return { rail: null, answer: m.text }
  let answer = ''
  let answerIdx = -1
  for (let i = segs.length - 1; i >= 0; i--) {
    const s = segs[i]
    if (s.kind === 'text' && s.text.trim()) {
      answer = s.text.replace(/^\s+/, '')
      answerIdx = i
      break
    }
  }
  const rail = segs.filter((s, i) => i !== answerIdx && !(s.kind === 'text' && !s.text.trim()))
  return { rail, answer }
}

function dayLabel(ms: number): string {
  return new Date(ms).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: '2-digit' })
}

type ChatListItem =
  | { kind: 'message'; message: ChatMessage }
  | { kind: 'tool-group'; id: string; messages: ChatMessage[] }

function isToolActivityLine(m: ChatMessage): boolean {
  if (m.role !== 'system' || !m.text.trim()) return false
  const text = m.text.replace(/\s+/g, ' ').trim()
  return (
    m.activityType === 'system' &&
    (text.includes(' ใช้ tool ') || /^tool\s+[^:]+:/i.test(text) || text.includes(' เรียก Owner Mode tool '))
  )
}

function isWorkStatusLine(m: ChatMessage): boolean {
  return m.role === 'system' && (m.flow?.kind === 'department_work' || m.flow?.kind === 'handoff')
}

function workStatusTone(m: ChatMessage): string {
  const event = String(m.flow?.refs?.event ?? '')
  if (event.includes('blocked') || event.includes('rejected') || event.includes('revising') || event.includes('escalated')) {
    return ACCENT_HEX.coral
  }
  if (event.includes('approved') || event.includes('started') || event.includes('delivered') || event.includes('review')) {
    return ACCENT_HEX.teal
  }
  return m.flow?.kind === 'handoff' ? ACCENT_HEX.lavender : ACCENT_HEX.sky
}

function groupToolActivityMessages(messages: ChatMessage[]): ChatListItem[] {
  const items: ChatListItem[] = []
  let group: ChatMessage[] = []
  const flush = () => {
    if (!group.length) return
    const first = group[0]
    const last = group[group.length - 1]
    items.push({ kind: 'tool-group', id: `tools-${first.id}-${last.id}`, messages: group })
    group = []
  }

  for (const message of messages) {
    if (isToolActivityLine(message)) {
      const last = group[group.length - 1]
      if (!last || dayLabel(last.ts) === dayLabel(message.ts)) {
        group.push(message)
        continue
      }
    }
    flush()
    items.push({ kind: 'message', message })
  }
  flush()
  return items
}

function mergeMessages(history: ChatMessage[] | null, live: ChatMessage[]): ChatMessage[] {
  if (!history) return live
  const byId = new Map<string, ChatMessage>()
  for (const message of history) byId.set(message.id, message)
  for (const message of live) byId.set(message.id, message)
  return [...byId.values()].sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id))
}

function compareMessages(a: ChatMessage, b: ChatMessage): number {
  return a.ts - b.ts || a.id.localeCompare(b.id)
}

function initialHistoryState(threadId: string): ThreadHistoryState {
  return {
    threadId,
    messages: [],
    loadingInitial: true,
    loadingOlder: false,
    loadingNewer: false,
    hasMoreBefore: false,
    error: null,
  }
}

function download(name: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

function fileSizeLabel(bytes?: number | null): string {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

function isImageAttachment(item: MessageAttachment): boolean {
  return item.kind === 'image' || Boolean(item.mime?.startsWith('image/'))
}

function isVideoAttachment(item: MessageAttachment): boolean {
  const mime = item.mime ?? ''
  return mime.startsWith('video/') || /\.(mp4|mov|m4v|webm)$/i.test(item.name ?? item.uri ?? '')
}

function videoReviewLabel(status?: string | null): { label: string; color: string } | null {
  if (!status) return null
  if (status === 'pending_video_review') return { label: 'รอตรวจคลิป', color: ACCENT_HEX.amber }
  if (status === 'approved_video_review') return { label: 'อนุมัติแล้ว', color: ACCENT_HEX.teal }
  if (status.includes('rejected')) return { label: 'ไม่อนุมัติ', color: ACCENT_HEX.coral }
  return { label: status.replaceAll('_', ' '), color: ACCENT_HEX.sky }
}

function compactContextValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function videoContextPayload(item: MessageAttachment, artifact: Artifact | null): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    artifactId: artifact?.id ?? item.artifactId ?? null,
    projectId: artifact?.projectId ?? item.projectId ?? null,
    videoProjectId: artifact?.videoProjectId ?? item.videoProjectId ?? null,
    assetId: artifact?.assetId ?? item.assetId ?? null,
    timelineId: artifact?.timelineId ?? item.timelineId ?? null,
    timelineVersion: artifact?.timelineVersion ?? item.timelineVersion ?? null,
    renderId: artifact?.renderId ?? item.renderId ?? null,
    mediaHandle: artifact?.mediaHandle ?? item.mediaHandle ?? null,
    contextTool: artifact?.contextTool ?? item.contextTool ?? null,
    contextArgs: artifact?.contextArgs ?? item.contextArgs ?? null,
    reviewStatus: artifact?.reviewStatus ?? item.reviewStatus ?? null,
    videoReviewId: artifact?.videoReviewId ?? item.videoReviewId ?? null,
    approvalId: artifact?.approvalId ?? item.approvalId ?? null,
  }
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== null && value !== undefined && value !== ''),
  )
}

function videoContextRows(context: Record<string, unknown>): { key: string; label: string; value: string }[] {
  const rows: { key: string; label: string; value: string }[] = []
  const push = (key: string, label: string) => {
    const value = compactContextValue(context[key])
    if (value) rows.push({ key, label, value })
  }
  push('artifactId', 'Artifact')
  push('projectId', 'Project')
  if (context.videoProjectId && context.videoProjectId !== context.projectId) push('videoProjectId', 'Video project')
  push('timelineId', 'Timeline')
  push('timelineVersion', 'Version')
  push('renderId', 'Render')
  push('assetId', 'Asset')
  push('mediaHandle', 'Handle')
  push('contextTool', 'Tool')
  push('videoReviewId', 'Review')
  push('approvalId', 'Approval')
  return rows
}

function prettyContextJson(context: Record<string, unknown>): string {
  try {
    return JSON.stringify(context, null, 2)
  } catch {
    return '{}'
  }
}

function attachmentIcon(item: MessageAttachment): string {
  const mime = item.mime ?? ''
  if (isVideoAttachment(item)) return 'VID'
  if (isImageAttachment(item)) return '▧'
  if (mime.includes('pdf')) return 'PDF'
  if (mime.includes('sheet') || item.kind === 'dataset') return 'CSV'
  if (mime.startsWith('text/') || item.kind === 'code' || item.kind === 'report') return 'TXT'
  if (item.kind === 'doc') return 'DOC'
  return 'FILE'
}

function previewSnippet(text: string): string {
  const normalized = text.replace(/\r\n/g, '\n').trim()
  return normalized.length > 900 ? `${normalized.slice(0, 900).trimEnd()}\n…` : normalized
}

type PreviewState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; content: string }
  | { status: 'empty' }
  | { status: 'error' }

function timeLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })
}

function deptDisplayName(deptId: string | null | undefined, deptNameById: Map<string, string>): string | null {
  if (!deptId) return null
  if (deptId === 'exec') return 'ผู้บริหาร'
  return deptNameById.get(deptId) ?? deptId
}

function roleLabel(m: ChatMessage, deptNameById: Map<string, string>): string {
  if (m.role === 'user') return 'ผู้ใช้'
  if (m.role === 'executive') return 'ผู้บริหาร'
  if (m.role === 'agent') {
    const name = m.participant?.departmentName ?? deptDisplayName(m.departmentId, deptNameById)
    return name ? `แผนก${name}` : 'แผนก'
  }
  return 'ระบบ'
}

function routeLabel(m: ChatMessage, deptNameById: Map<string, string>): string | null {
  const target = m.input?.routedDepartmentId
  if (!target) return null
  if (target === 'exec') return 'ถึงผู้บริหาร'
  const name = deptDisplayName(target, deptNameById)
  return name ? `ถึงฝ่าย${name}` : `ถึง ${target}`
}

function liveStatusLabel(m: ChatMessage): string {
  if (m.toolRuns?.some((r) => r.status === 'running')) return 'กำลังใช้เครื่องมือ'
  if (m.status === 'queued') return 'อยู่ในคิว กำลังเตรียมบริบท'
  if (m.streaming) return 'กำลังคิดและส่งคำตอบแบบสด'
  return 'กำลังคิดและเตรียมคำตอบ'
}

function toolNameFromActivity(text: string): string {
  return (
    /ใช้ tool\s+([^:]+):/i.exec(text)?.[1] ??
    /^tool\s+([^:]+):/i.exec(text)?.[1] ??
    /เรียก Owner Mode tool\s+([^:]+):/i.exec(text)?.[1] ??
    'tool'
  ).trim()
}

function isFailedToolActivity(text: string): boolean {
  const statusCode = /->\s*(\d{3})\b/.exec(text)?.[1]
  if (statusCode) return Number(statusCode) >= 400
  return /\b(failed|error|cancelled)\b|ล้มเหลว/i.test(text)
}

function ToolActivityGroup({ messages, accent }: { messages: ChatMessage[]; accent: AccentName }) {
  const failed = messages.filter((m) => isFailedToolActivity(m.text)).length
  const ok = messages.length - failed
  const tools = [...new Set(messages.map((m) => toolNameFromActivity(m.text)))].slice(0, 2)
  const preview = [
    tools.join(' · '),
    ok > 0 ? `${ok} สำเร็จ` : '',
    failed > 0 ? `${failed} ผิดพลาด` : '',
  ].filter(Boolean).join(' · ')
  const recovered = failed > 0 && ok > 0
  const statusColor = failed ? (recovered ? ACCENT_HEX.honey : ACCENT_HEX.coral) : ACCENT_HEX.teal
  return (
    <details className="group my-1.5 min-w-0 max-w-full">
      <summary
        className="mx-auto flex min-h-8 w-full max-w-[calc(100%-1rem)] cursor-pointer list-none items-center gap-2 overflow-hidden rounded-lg px-2.5 py-1.5 text-[12px] transition-colors hover:bg-[var(--color-surface)]"
        style={{
          background: withAlpha(ACCENT_HEX[accent], 0.05),
          border: `1px solid ${withAlpha(ACCENT_HEX[accent], 0.16)}`,
          color: 'var(--color-cream-dim)',
        }}
      >
        <span className="shrink-0 transition-transform group-open:rotate-90">▸</span>
        <span className="shrink-0 font-medium text-[var(--color-cream)]">เครื่องมือ {messages.length} รายการ</span>
        <span className="min-w-0 flex-1 truncate text-[var(--color-cream-faint)]">· {preview}</span>
        <span className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium" style={{ background: withAlpha(statusColor, 0.12), color: statusColor }}>
          {failed ? (recovered ? 'retry สำเร็จ' : 'มีข้อผิดพลาด') : 'ย่อไว้'}
        </span>
      </summary>
      <div
        className="mx-auto mt-1 w-full max-w-[calc(100%-1rem)] space-y-0.5 rounded-lg px-2.5 py-2 text-[12px]"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
      >
        {messages.map((m) => {
          const lineFailed = isFailedToolActivity(m.text)
          const c = lineFailed ? ACCENT_HEX.coral : ACCENT_HEX.teal
          return (
            <div id={`msg-${m.id}`} key={m.id} className="grid min-w-0 grid-cols-[auto_1fr_auto] items-start gap-2 py-0.5">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: c }} />
              <span className="min-w-0 break-words [overflow-wrap:anywhere] text-[var(--color-cream-dim)]">{m.text}</span>
              <span className="shrink-0 text-[11px] text-[var(--color-cream-faint)]">{timeLabel(m.ts)}</span>
            </div>
          )
        })}
      </div>
    </details>
  )
}

function AttachmentCard({
  item,
  hex,
  mine,
}: {
  item: MessageAttachment
  hex: string
  mine: boolean
}) {
  const label = item.name ?? item.artifactId ?? 'ไฟล์แนบ'
  const meta = [item.kind, item.mime, fileSizeLabel(item.sizeBytes)].filter(Boolean).join(' · ')
  const isImage = isImageAttachment(item)
  const openHref = item.artifactId ? client.artifactOpenUrl(item.artifactId) : item.uri ?? undefined
  const downloadHref = item.artifactId ? client.artifactDownloadUrl(item.artifactId) : item.uri ?? undefined
  const isVideo = isVideoAttachment(item)
  const [expanded, setExpanded] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [contextCopied, setContextCopied] = useState(false)
  const [preview, setPreview] = useState<PreviewState>({ status: 'idle' })
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const requestedPreview = useRef<string | null>(null)
  const canPreview = Boolean(item.artifactId) && !isImage

  useEffect(() => {
    if (!expanded || !canPreview || !item.artifactId || requestedPreview.current === item.artifactId) return
    let live = true
    requestedPreview.current = item.artifactId
    setPreview({ status: 'loading' })
    client
      .getArtifactPreview(item.artifactId)
      .then((res) => {
        if (!live) return
        const content = typeof res.content === 'string' ? previewSnippet(res.content) : ''
        setPreview(content ? { status: 'ready', content } : { status: 'empty' })
      })
      .catch(() => {
        if (!live) return
        requestedPreview.current = null
        setPreview({ status: 'error' })
      })
    return () => {
      live = false
    }
  }, [canPreview, expanded, item.artifactId])

  useEffect(() => {
    if (!isVideo || !item.artifactId) return
    let live = true
    let timer: number | undefined
    const loadArtifact = () => {
      client
        .getArtifact(item.artifactId!)
        .then((res) => {
          if (live) setArtifact(res)
        })
        .catch(() => undefined)
    }
    loadArtifact()
    if ((artifact?.reviewStatus ?? item.reviewStatus) === 'pending_video_review') {
      timer = window.setInterval(loadArtifact, 2000)
    }
    return () => {
      live = false
      if (timer) window.clearInterval(timer)
    }
  }, [artifact?.reviewStatus, isVideo, item.artifactId, item.reviewStatus])

  const shellStyle = {
    border: `1px solid ${withAlpha(hex, 0.28)}`,
    background: withAlpha(hex, mine ? 0.1 : 0.08),
  }
  const actionCls = 'rounded-md px-2 py-1 text-[11px] transition-colors hover:bg-[var(--color-surface)]'
  const muted = 'text-[var(--color-cream-faint)]'
  const reviewStatus = artifact?.reviewStatus ?? item.reviewStatus ?? null
  const approvalId = artifact?.approvalId ?? item.approvalId ?? null
  const review = videoReviewLabel(reviewStatus)
  const videoContext = isVideo ? videoContextPayload(item, artifact) : {}
  const videoRows = isVideo ? videoContextRows(videoContext) : []
  const videoContextText = videoRows.length || videoContext.contextArgs ? prettyContextJson(videoContext) : ''
  const contextArgsText = compactContextValue(videoContext.contextArgs)
  const copyVideoContext = () => {
    if (!videoContextText) return
    const write = navigator.clipboard?.writeText(videoContextText)
    if (!write) return
    void write
      .then(() => {
        setContextCopied(true)
        window.setTimeout(() => setContextCopied(false), 1600)
      })
      .catch(() => setContextCopied(false))
  }

  if (isVideo && openHref) {
    const pendingApprovalId = reviewStatus === 'pending_video_review' && approvalId ? approvalId : null
    return (
      <div className="min-w-[240px] max-w-[360px] overflow-hidden rounded-lg" style={shellStyle} title={meta || label} data-testid="video-review-card">
        <video
          src={openHref}
          controls
          preload="metadata"
          className="max-h-72 w-full bg-black object-contain"
        />
        <div className="space-y-1.5 px-2.5 py-2 text-[12px]">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0 text-[10px] font-semibold" style={{ color: hex }}>VID</span>
            <span className="min-w-0 flex-1 truncate">{label}</span>
            {review && (
              <span className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ color: review.color, background: withAlpha(review.color, 0.14) }}>
                {review.label}
              </span>
            )}
          </div>
          {meta && <div className="truncate text-[10px] text-[var(--color-cream-faint)]">{meta}</div>}
          {videoRows.length > 0 && (
            <div className="space-y-1.5 border-t pt-1.5" style={{ borderColor: withAlpha(hex, 0.18) }}>
              <div className="flex flex-wrap gap-1.5" data-testid="video-context-summary">
                {videoRows.slice(0, 4).map((row) => (
                  <span
                    key={row.key}
                    className="flex max-w-full items-center gap-1 overflow-hidden rounded-md px-1.5 py-0.5 text-[10px]"
                    style={{ background: 'rgba(0,0,0,0.16)' }}
                    title={`${row.label}: ${row.value}`}
                  >
                    <span className="shrink-0 text-[var(--color-cream-faint)]">{row.label}</span>
                    <span className="min-w-0 truncate text-[var(--color-cream-dim)]">{row.value}</span>
                  </span>
                ))}
              </div>
              {contextOpen && (
                <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[10px]" data-testid="video-context-details">
                  {videoRows.slice(4).map((row) => (
                    <div key={row.key} className="contents">
                      <span className="text-[var(--color-cream-faint)]">{row.label}</span>
                      <span className="min-w-0 break-all text-[var(--color-cream-dim)]">{row.value}</span>
                    </div>
                  ))}
                  {contextArgsText && (
                    <div className="contents">
                      <span className="text-[var(--color-cream-faint)]">Args</span>
                      <span className="min-w-0 whitespace-pre-wrap break-all text-[var(--color-cream-dim)]">{contextArgsText}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div className={`flex flex-wrap items-center gap-1 ${muted}`}>
            <a href={openHref} target="_blank" rel="noreferrer" className={actionCls}>เปิด</a>
            {downloadHref && <a href={downloadHref} download className={actionCls}>ดาวน์โหลด</a>}
            {videoContextText && (
              <>
                <button
                  type="button"
                  className={actionCls}
                  data-testid="video-context-toggle"
                  onClick={() => setContextOpen((v) => !v)}
                >
                  {contextOpen ? 'ซ่อน context' : 'context'}
                </button>
                <button
                  type="button"
                  className={actionCls}
                  data-testid="video-context-copy"
                  onClick={copyVideoContext}
                >
                  {contextCopied ? 'คัดลอกแล้ว' : 'คัดลอก context'}
                </button>
              </>
            )}
            {pendingApprovalId && (
              <>
                <button
                  type="button"
                  className={actionCls}
                  style={{ color: ACCENT_HEX.teal }}
                  data-testid="video-review-approve"
                  data-video-approval-id={pendingApprovalId}
                  onClick={() => client.resolveApproval(pendingApprovalId, 'approved')}
                >
                  อนุมัติ
                </button>
                <button
                  type="button"
                  className={actionCls}
                  style={{ color: ACCENT_HEX.coral }}
                  data-testid="video-review-reject"
                  data-video-approval-id={pendingApprovalId}
                  onClick={() => client.resolveApproval(pendingApprovalId, 'rejected')}
                >
                  ปฏิเสธ
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (isImage && openHref) {
    return (
      <div className="max-w-[240px] overflow-hidden rounded-lg" style={shellStyle} title={meta || label}>
        <a href={openHref} target="_blank" rel="noreferrer" className="block">
          <img src={openHref} alt={label} loading="lazy" className="aspect-square w-full bg-black/20 object-cover" />
        </a>
        <div className="space-y-1.5 px-2 py-1.5 text-[12px]">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0" style={{ color: hex }}>▧</span>
            <span className="min-w-0 truncate">{label}</span>
            {item.sizeBytes ? <span className="shrink-0 text-[10px] opacity-70">{fileSizeLabel(item.sizeBytes)}</span> : null}
          </div>
          <div className={`flex items-center gap-1 ${muted}`}>
            <a href={openHref} target="_blank" rel="noreferrer" className={actionCls}>เปิด</a>
            {downloadHref && <a href={downloadHref} download className={actionCls}>ดาวน์โหลด</a>}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-w-[180px] max-w-[280px] rounded-lg px-2.5 py-2 text-[12px]" style={shellStyle} title={meta || label}>
      <div className="flex min-w-0 items-start gap-2">
        <span className="mt-0.5 w-8 shrink-0 text-[10px] font-semibold" style={{ color: hex }}>{attachmentIcon(item)}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate">{label}</div>
          {meta && <div className="truncate text-[10px] text-[var(--color-cream-faint)]">{meta}</div>}
        </div>
      </div>
      <div className={`mt-1.5 flex flex-wrap items-center gap-1 ${muted}`}>
        {canPreview && (
          <button type="button" className={actionCls} onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'ซ่อนพรีวิว' : 'พรีวิว'}
          </button>
        )}
        {openHref && <a href={openHref} target="_blank" rel="noreferrer" className={actionCls}>เปิด</a>}
        {downloadHref && <a href={downloadHref} download className={actionCls}>ดาวน์โหลด</a>}
      </div>
      {expanded && (
        <div className="mt-2 max-h-36 overflow-y-auto rounded-md px-2 py-1.5 text-[11px] leading-relaxed whitespace-pre-wrap" style={{ background: 'rgba(0,0,0,0.18)' }}>
          {preview.status === 'loading'
            ? 'กำลังโหลดพรีวิว…'
            : preview.status === 'ready'
              ? preview.content
              : preview.status === 'empty'
                ? 'ไม่มีเนื้อหาพรีวิว'
                : preview.status === 'error'
                  ? 'พรีวิวไฟล์นี้ไม่ได้'
                  : null}
        </div>
      )}
    </div>
  )
}

function AttachmentStrip({
  items,
  accent,
  mine,
}: {
  items?: MessageAttachment[]
  accent: AccentName
  mine: boolean
}) {
  if (!items?.length) return null
  const hex = mine ? ACCENT_HEX.amber : ACCENT_HEX[accent]
  return (
    <div className="mt-2 flex max-w-full flex-wrap gap-2">
      {items.map((item) => {
        const key = item.artifactId ?? item.uri ?? item.name ?? 'attachment'
        return <AttachmentCard key={key} item={item} hex={hex} mine={mine} />
      })}
    </div>
  )
}

/* ----------------------------- one message ----------------------------- */

function MessageRow({
  m,
  showHeader,
  accent,
  emoji,
  deptNameById,
  isLastAssistant,
  onAction,
  onFollowUp,
  onOpenCitation,
}: {
  m: ChatMessage
  showHeader: boolean
  accent: AccentName
  emoji: string
  deptNameById: Map<string, string>
  isLastAssistant: boolean
  onAction: (a: string, m: ChatMessage, arg?: string) => void
  onFollowUp: (text: string) => void
  onOpenCitation: (c: MessageCitation) => void
}) {
  const mine = m.role === 'user'
  const failed = m.status === 'failed'
  const meta = [roleLabel(m, deptNameById), routeLabel(m, deptNameById), timeLabel(m.ts)].filter(
    (item): item is string => Boolean(item),
  )
  // Break the assistant turn into its chronological rail (think → tools → …) and
  // the final answer, which renders at the very bottom of the bubble.
  const { rail, answer } = mine ? { rail: null, answer: m.text } : splitTurnSegments(m)
  const tailActive = !!m.streaming && !answer.trim()

  if (m.role === 'system') {
    if (isWorkStatusLine(m)) {
      const tone = workStatusTone(m)
      const steps = m.flow?.steps ?? []
      return (
        <div id={`msg-${m.id}`} className="my-2 flex justify-center">
          <div
            className="min-w-0 max-w-[92%] rounded-lg px-3 py-2 text-[12px]"
            style={{
              background: withAlpha(tone, 0.1),
              border: `1px solid ${withAlpha(tone, 0.28)}`,
              color: 'var(--color-cream-dim)',
            }}
          >
            <div className="flex min-w-0 items-start gap-2">
              <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full" style={{ background: tone }} />
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span className="font-medium" style={{ color: 'var(--color-cream)' }}>
                    {m.flow?.kind === 'handoff' ? 'ส่งต่องาน' : 'สถานะงาน'}
                  </span>
                  <span className="min-w-0 break-words">{m.text}</span>
                  <span className="shrink-0 opacity-70">{timeLabel(m.ts)}</span>
                </div>
                {steps.length > 0 && (
                  <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1 text-[11px] text-[var(--color-cream-faint)]">
                    {steps.map((s, i) => (
                      <span key={`${s.kind}-${i}`} className="inline-flex min-w-0 items-center gap-1">
                        {i > 0 && <span style={{ color: withAlpha(tone, 0.85) }}>→</span>}
                        <span
                          className="max-w-[160px] truncate rounded-md px-1.5 py-0.5"
                          style={{ background: withAlpha(tone, 0.12) }}
                          title={s.label || s.kind}
                        >
                          {s.label || s.kind}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <NoticeCards m={m} onResolveApproval={(id, d) => client.resolveApproval(id, d)} />
          </div>
        </div>
      )
    }
    return (
      <div id={`msg-${m.id}`} className="my-1.5">
        {m.text && (
          <div className="text-center text-[12px] text-[var(--color-cream-faint)]">
            <span>{m.text}</span>
            <span className="ml-1.5 opacity-70">{timeLabel(m.ts)}</span>
          </div>
        )}
        <NoticeCards m={m} onResolveApproval={(id, d) => client.resolveApproval(id, d)} />
        <FlowCard m={m} />
      </div>
    )
  }

  return (
    <motion.div
      id={`msg-${m.id}`}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={`group flex items-end gap-2.5 ${mine ? 'flex-row-reverse' : ''}`}
    >
      <div className="w-8 shrink-0">{!mine && showHeader && <Avatar emoji={emoji} accent={accent} size={32} />}</div>

      <div className={`flex min-w-0 flex-col ${mine ? 'items-end' : 'items-start'}`} style={{ maxWidth: '86%' }}>
        {showHeader && (
          <span className={`mb-1 flex max-w-full flex-wrap items-center gap-1 px-1 text-[12px] font-medium text-[var(--color-cream-dim)] ${mine ? 'justify-end' : ''}`}>
            <span>{m.authorName}</span>
            {meta.map((item) => (
              <span key={item} className="rounded-full px-1.5 py-0.5 text-[10px] font-normal text-[var(--color-cream-faint)]" style={{ background: 'rgba(255,255,255,0.06)' }}>
                {item}
              </span>
            ))}
            {m.pinned && <span title="ปักหมุด">📌</span>}
          </span>
        )}

        <div
          className="max-w-full min-w-0 rounded-2xl px-3.5 py-2.5 break-words [overflow-wrap:anywhere]"
          style={
            mine
              ? {
                  background: withAlpha(failed ? '#f0735f' : ACCENT_HEX.amber, 0.18),
                  border: `1px solid ${withAlpha(failed ? '#f0735f' : ACCENT_HEX.amber, 0.34)}`,
                  color: 'var(--color-cream)',
                  borderBottomRightRadius: 6,
                }
              : {
                  background: 'var(--color-surface-2)',
                  border: '1px solid var(--color-line-soft)',
                  color: 'var(--color-cream)',
                  borderBottomLeftRadius: 6,
                }
          }
        >
          <QuotePreview m={m} />

          {/* one round, in chronological order: think → use tools → think again
              → … rendered above, and the final answer below */}
          {!mine &&
            (rail ? (
              <TurnTimeline segments={rail} accent={accent} tailActive={tailActive} />
            ) : (
              <TurnActivity m={m} accent={accent} />
            ))}

          {m.pending ? (
            <TypingDots color={ACCENT_HEX[accent]} label={liveStatusLabel(m)} />
          ) : !mine || m.contentFormat === 'markdown' || m.render?.format === 'markdown' ? (
            // Assistant output is always treated as markdown (matching the
            // backend, which flags agent/executive turns as markdown) so fenced
            // code blocks, tables, and lists always render — and code blocks get
            // a one-click copy button. User text stays verbatim.
            <Markdown text={answer} />
          ) : (
            <div className="whitespace-pre-wrap text-[14px] leading-[1.65]">{answer}</div>
          )}
          {m.streaming && !m.pending && (
            <motion.span
              className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 rounded-sm"
              style={{ background: ACCENT_HEX[accent] }}
              animate={{ opacity: [1, 0.2, 1] }}
              transition={{ duration: 0.9, repeat: Infinity }}
            />
          )}
          <AttachmentStrip items={m.attachments} accent={accent} mine={mine} />

          <NoticeCards m={m} onResolveApproval={(id, d) => client.resolveApproval(id, d)} />
          <FlowCard m={m} />
          {!mine && <Citations render={m.render} onOpen={onOpenCitation} />}
        </div>

        <Reactions m={m} onToggle={(e, on) => onAction(on ? 'react' : 'unreact', m, e)} />

        {failed && (
          <button type="button" onClick={() => onAction('retry', m)} className="mt-0.5 px-1 text-[12px]" style={{ color: '#f0735f' }}>
            ↻ ส่งใหม่
          </button>
        )}

        {!m.pending && !m.streaming && !failed && <MetaFooter m={m} />}

        {isLastAssistant && <FollowUps items={m.suggestedFollowUps} accent={accent} onPick={onFollowUp} />}

        {!m.pending && !m.streaming && (
          <div
            className={`mt-1 flex items-center gap-2 px-1 text-[12px] text-[var(--color-cream-faint)] opacity-0 transition-opacity group-hover:opacity-100 ${mine ? 'flex-row-reverse' : ''}`}
          >
            <QuickReactBar onReact={(e) => onAction('react', m, e)} />
            <button type="button" onClick={() => onAction('copy', m)} className="hover:text-[var(--color-cream)]">คัดลอก</button>
            <button type="button" onClick={() => onAction('quote', m)} className="hover:text-[var(--color-cream)]">อ้างถึง</button>
            <button type="button" onClick={() => onAction('pin', m)} className="hover:text-[var(--color-cream)]">
              {m.pinned ? 'เลิกปัก' : 'ปักหมุด'}
            </button>
            {mine ? (
              <button type="button" onClick={() => onAction('edit', m)} className="hover:text-[var(--color-cream)]">แก้ไข</button>
            ) : (
              <>
                <button type="button" onClick={() => onAction('regenerate', m)} className="hover:text-[var(--color-cream)]">สร้างใหม่</button>
                <button type="button" onClick={() => onAction('promote', m)} className="hover:text-[var(--color-cream)]">→ ความจำ</button>
              </>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}

/* ------------------------------- panel ------------------------------- */

export function ChatPanel({
  threadId,
  targetDepartmentId,
  accent,
  emoji,
  placeholder,
}: {
  threadId: string
  targetDepartmentId?: string | null
  accent: AccentName
  emoji: string
  placeholder: string
}) {
  const snapshotMessages = useSelector((s) => s.threads[threadId] ?? EMPTY)
  const departments = useSelector((s) => s.departments)
  const deptNameById = useMemo(
    () => new Map(departments.map((d) => [d.id, d.name])),
    [departments],
  )
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const prevLen = useRef(snapshotMessages.length)
  const seedN = useRef(0)
  const suppressUnseenForPrepend = useRef(false)
  const newerCursorRef = useRef<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [unseen, setUnseen] = useState(0)
  const [quoted, setQuoted] = useState<ChatMessage | null>(null)
  const [editing, setEditing] = useState<ChatMessage | null>(null)
  const [seed, setSeed] = useState<{ text: string; nonce: number } | undefined>()
  const [cost, setCost] = useState<ThreadCostSummary | null>(null)
  const [starters, setStarters] = useState<PromptStarter[]>([])
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [hits, setHits] = useState<{ id: string; snippet: string; author: string }[] | null>(null)
  const [history, setHistory] = useState<ThreadHistoryState>(() => initialHistoryState(threadId))
  const setRightTab = useUI((s) => s.setRightTab)
  const historyState = history?.threadId === threadId ? history : null
  const historyMessages = historyState?.messages ?? null
  const messages = useMemo(() => mergeMessages(historyMessages, snapshotMessages), [historyMessages, snapshotMessages])

  const busy = useMemo(() => messages.some((m) => m.pending || m.streaming), [messages])

  const visibleMessages = messages
  const chatItems = useMemo(() => groupToolActivityMessages(visibleMessages), [visibleMessages])

  const scrollToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  useEffect(() => {
    let live = true
    newerCursorRef.current = null
    client
      .getThreadMessages(threadId, { limit: HISTORY_PAGE_SIZE })
      .then((loaded) => {
        if (!live) return
        setHistory({
          threadId,
          messages: loaded,
          loadingInitial: false,
          loadingOlder: false,
          loadingNewer: false,
          hasMoreBefore: loaded.length === HISTORY_PAGE_SIZE,
          error: null,
        })
      })
      .catch((err) => {
        if (!live) return
        setHistory({
          threadId,
          messages: [],
          loadingInitial: false,
          loadingOlder: false,
          loadingNewer: false,
          hasMoreBefore: false,
          error: err instanceof Error ? err.message : String(err),
        })
      })
    return () => {
      live = false
    }
  }, [threadId])

  const restoreScrollAfterPrepend = (previousTop: number, previousHeight: number) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const el = scrollRef.current
        if (!el) return
        el.scrollTop = previousTop + (el.scrollHeight - previousHeight)
      })
    })
  }

  const loadOlder = () => {
    const state = historyState
    const first = state?.messages[0] ?? messages[0]
    if (!state || !first || state.loadingInitial || state.loadingOlder || !state.hasMoreBefore) return
    const el = scrollRef.current
    const previousTop = el?.scrollTop ?? 0
    const previousHeight = el?.scrollHeight ?? 0
    suppressUnseenForPrepend.current = true
    setHistory((current) =>
      current?.threadId === threadId ? { ...current, loadingOlder: true, error: null } : current,
    )
    client
      .getThreadMessages(threadId, {
        limit: HISTORY_PAGE_SIZE,
        before: first.ts,
        beforeId: first.id,
      })
      .then((older) => {
        setHistory((current) => {
          if (!current || current.threadId !== threadId) return current
          return {
            ...current,
            messages: mergeMessages(older, current.messages),
            loadingOlder: false,
            hasMoreBefore: older.length === HISTORY_PAGE_SIZE,
            error: null,
          }
        })
        restoreScrollAfterPrepend(previousTop, previousHeight)
      })
      .catch((err) => {
        suppressUnseenForPrepend.current = false
        setHistory((current) =>
          current?.threadId === threadId
            ? {
                ...current,
                loadingOlder: false,
                error: err instanceof Error ? err.message : String(err),
              }
            : current,
        )
      })
  }

  const latestHistory = historyState?.messages[historyState.messages.length - 1] ?? null
  const latestSnapshot = snapshotMessages[snapshotMessages.length - 1] ?? null
  useEffect(() => {
    if (
      !historyState ||
      historyState.loadingInitial ||
      historyState.loadingNewer ||
      !latestHistory ||
      !latestSnapshot ||
      compareMessages(latestSnapshot, latestHistory) <= 0
    ) {
      return
    }
    const cursor = `${threadId}:${latestHistory.ts}:${latestHistory.id}`
    if (newerCursorRef.current === cursor) return
    newerCursorRef.current = cursor
    setHistory((current) =>
      current?.threadId === threadId ? { ...current, loadingNewer: true, error: null } : current,
    )
    let live = true
    client
      .getThreadMessages(threadId, {
        limit: HISTORY_PAGE_SIZE,
        after: latestHistory.ts,
        afterId: latestHistory.id,
      })
      .then((newer) => {
        if (!live) return
        newerCursorRef.current = newer.length ? null : cursor
        setHistory((current) => {
          if (!current || current.threadId !== threadId) return current
          return {
            ...current,
            messages: mergeMessages(current.messages, newer),
            loadingNewer: false,
            error: null,
          }
        })
      })
      .catch((err) => {
        if (!live) return
        setHistory((current) =>
          current?.threadId === threadId
            ? {
                ...current,
                loadingNewer: false,
                error: err instanceof Error ? err.message : String(err),
              }
            : current,
        )
      })
    return () => {
      live = false
    }
  }, [historyState, latestHistory, latestSnapshot, threadId])

  useEffect(() => {
    const grew = messages.length - prevLen.current
    prevLen.current = messages.length
    if (suppressUnseenForPrepend.current) {
      suppressUnseenForPrepend.current = false
      return
    }
    // Defer to after paint so we don't setState synchronously inside the effect.
    const id = requestAnimationFrame(() => {
      if (atBottom) {
        scrollToBottom()
        setUnseen(0)
      } else if (grew > 0) {
        setUnseen((u) => u + grew)
      }
    })
    return () => cancelAnimationFrame(id)
  }, [messages.length, atBottom])

  // Follow live-streaming growth: the message count is unchanged while tokens
  // stream into the same bubble, so track content length too. Scroll only (no
  // setState) to stay clear of the set-state-in-effect rule.
  const streamSig = useMemo(() => {
    const last = messages[messages.length - 1]
    if (!last) return ''
    return `${last.id}:${last.text.length}:${last.reasoning?.length ?? 0}:${last.toolRuns?.length ?? 0}`
  }, [messages])
  useEffect(() => {
    if (!atBottom) return
    const id = requestAnimationFrame(scrollToBottom)
    return () => cancelAnimationFrame(id)
  }, [streamSig, atBottom])

  useEffect(() => {
    let live = true
    const t = window.setTimeout(() => {
      client.getThreadCost(threadId).then((c) => live && setCost(c)).catch(() => undefined)
    }, 400)
    return () => {
      live = false
      window.clearTimeout(t)
    }
  }, [threadId, messages.length])

  useEffect(() => {
    if (messages.length > 0) return
    let live = true
    client.getPromptStarters(threadId).then((s) => live && setStarters(s)).catch(() => undefined)
    return () => {
      live = false
    }
  }, [threadId, messages.length])

  const pushSeed = (text: string) => {
    seedN.current += 1
    setSeed({ text, nonce: seedN.current })
  }

  const lastAssistantId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (!m.pending && !m.streaming && (m.role === 'agent' || m.role === 'executive')) return m.id
    }
    return null
  }, [messages])

  const onSend = (text: string, effort: ThinkingEffort | '', attachments: MessageAttachment[]) => {
    const eff = effort || null
    if (editing) {
      void client.editMessage(threadId, editing.id, text, eff ?? undefined)
      setEditing(null)
    } else {
      void client.sendMessage(threadId, text, {
        thinkingEffort: eff,
        targetDepartmentId,
        quoteMessageId: quoted?.id ?? null,
        attachments,
      })
      setQuoted(null)
    }
    setAtBottom(true)
  }

  const onAction = (a: string, m: ChatMessage, arg?: string) => {
    switch (a) {
      case 'copy':
        void navigator.clipboard?.writeText(m.text)
        break
      case 'quote':
        setEditing(null)
        setQuoted(m)
        break
      case 'pin':
        void client.pinMessage(threadId, m.id, !m.pinned)
        break
      case 'react':
        if (arg) void client.toggleReaction(threadId, m.id, arg, true)
        break
      case 'unreact':
        if (arg) void client.toggleReaction(threadId, m.id, arg, false)
        break
      case 'regenerate':
        void client.regenerateMessage(threadId, m.id)
        break
      case 'promote':
        void client.promoteMessage(threadId, m.id)
        break
      case 'edit':
        setQuoted(null)
        setEditing(m)
        pushSeed(m.text)
        break
      case 'retry':
        if (m.clientMessageId) void client.retryFailed(threadId, m.clientMessageId)
        else void client.retryMessage(threadId, m.id)
        break
    }
  }

  const editLast = () => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user' && messages[i].status !== 'failed') {
        onAction('edit', messages[i])
        return
      }
    }
  }

  const runSearch = (q: string) => {
    setSearchQ(q)
    if (!q.trim()) {
      setHits(null)
      return
    }
    client
      .searchThread(threadId, q, 12)
      .then((r) => setHits((r.hits ?? []).map((h) => ({ id: h.message.id, snippet: h.snippet, author: h.message.authorName }))))
      .catch(() => setHits(null))
  }

  const jumpTo = (id: string) => {
    const el = document.getElementById(`msg-${id}`)
    const details = el?.closest('details')
    if (details instanceof HTMLDetailsElement) details.open = true
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setSearchOpen(false)
  }

  const doExport = (format: 'md' | 'json') => {
    void client.exportThread(threadId, format).then((r) => download(r.filename, r.content, r.contentType))
  }

  const openCitation = () => setRightTab('memory')

  const toolBtn =
    'flex items-center gap-1 rounded-lg px-1.5 py-1 text-[11px] text-[var(--color-cream-faint)] transition-colors hover:bg-[var(--color-surface)] hover:text-[var(--color-cream-dim)]'

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="mb-1 flex items-center gap-2 text-[12px] text-[var(--color-cream-faint)]">
        {cost?.totalUsd ? (
          <span className="flex items-center gap-1" title="ค่าใช้จ่ายรวมของบทสนทนานี้">💰 {money(cost.totalUsd)}</span>
        ) : (
          <span className="flex items-center gap-1 opacity-60">💬 บทสนทนา</span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <button type="button" onClick={() => setSearchOpen((v) => !v)} className={toolBtn} title="ค้นหาในแชต">🔍 ค้นหา</button>
          <button type="button" onClick={() => doExport('md')} className={toolBtn} title="ส่งออก Markdown">⬇ MD</button>
          <button type="button" onClick={() => doExport('json')} className={toolBtn} title="ส่งออก JSON">JSON</button>
        </div>
      </div>

      {searchOpen && (
        <div className="mb-1.5">
          <input
            autoFocus
            value={searchQ}
            onChange={(e) => runSearch(e.target.value)}
            placeholder="ค้นหาในบทสนทนา…"
            className="w-full rounded-xl px-3 py-2 text-[14px] text-[var(--color-cream)] outline-none"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
          />
          {hits && (
            <div className="mt-1 max-h-48 overflow-y-auto rounded-xl" style={{ border: '1px solid var(--color-line-soft)' }}>
              {hits.length === 0 && <div className="px-3 py-2 text-[13px] text-[var(--color-cream-faint)]">ไม่พบ</div>}
              {hits.map((h) => (
                <button key={h.id} type="button" onClick={() => jumpTo(h.id)} className="block w-full px-3 py-2 text-left text-[13px] hover:bg-[var(--color-surface)]">
                  <span className="text-[var(--color-cream-faint)]">{h.author}: </span>
                  {h.snippet}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget
          const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          setAtBottom(bottom)
          if (bottom) setUnseen(0)
          if (el.scrollTop < 96) loadOlder()
        }}
        className="min-h-0 flex-1 space-y-3 overflow-x-hidden overflow-y-auto px-0.5 py-1.5"
      >
        {historyState?.loadingInitial && messages.length === 0 && (
          <div className="mt-4 text-center text-[12px] text-[var(--color-cream-faint)]">กำลังโหลดประวัติ…</div>
        )}

        {messages.length > 0 && (historyState?.hasMoreBefore || historyState?.loadingOlder) && (
          <div className="sticky top-0 z-10 flex justify-center py-1">
            <button
              type="button"
              onClick={loadOlder}
              disabled={historyState.loadingInitial || historyState.loadingOlder}
              className="rounded-full border px-3 py-1 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
              style={{
                background: withAlpha(ACCENT_HEX[accent], 0.16),
                borderColor: withAlpha(ACCENT_HEX[accent], 0.32),
                color: 'var(--color-cream)',
              }}
            >
              {historyState.loadingOlder ? 'กำลังโหลด…' : `โหลดก่อนหน้า ${HISTORY_PAGE_SIZE} ข้อความ`}
            </button>
          </div>
        )}

        {historyState?.error && (
          <div className="rounded-xl border px-3 py-2 text-[12px] text-[var(--color-cream-faint)]" style={{ borderColor: 'var(--color-line-soft)' }}>
            โหลดประวัติไม่สำเร็จ: {historyState.error}
          </div>
        )}

        {messages.length === 0 && !historyState?.loadingInitial && (
          <div className="mt-8 px-1">
            <div className="text-center text-base text-[var(--color-cream-dim)]">เริ่มสนทนาได้เลย</div>
            <div className="mt-1 text-center text-[12px] text-[var(--color-cream-faint)]">พิมพ์ข้อความ หรือเลือกหัวข้อเริ่มต้นด้านล่าง</div>
            {starters.length > 0 && (
              <div className="mt-4 flex flex-col gap-2">
                {starters.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => pushSeed(s.text)}
                    className="rounded-xl px-3.5 py-2.5 text-left text-[14px] leading-snug transition-colors hover:border-[var(--color-line)]"
                    style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)', color: 'var(--color-cream)' }}
                  >
                    <span className="mr-1.5" style={{ color: ACCENT_HEX[accent] }}>✦</span>
                    {s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {chatItems.map((item, i) => {
          const m = item.kind === 'message' ? item.message : item.messages[0]
          const prev = chatItems[i - 1]
          const prevMessage = prev?.kind === 'message' ? prev.message : prev?.messages[prev.messages.length - 1]
          const newDay = !prevMessage || dayLabel(prevMessage.ts) !== dayLabel(m.ts)
          const showHeader =
            newDay || !prevMessage || prevMessage.role !== m.role || prevMessage.authorName !== m.authorName || m.ts - prevMessage.ts > 5 * 60_000
          return (
            <div key={item.kind === 'message' ? m.id : item.id}>
              {newDay && (
                <div className="my-3 flex items-center gap-2">
                  <div className="h-px flex-1" style={{ background: 'var(--color-line-soft)' }} />
                  <span className="text-[11px] text-[var(--color-cream-faint)]">{dayLabel(m.ts)}</span>
                  <div className="h-px flex-1" style={{ background: 'var(--color-line-soft)' }} />
                </div>
              )}
              {item.kind === 'tool-group' ? (
                <ToolActivityGroup messages={item.messages} accent={accent} />
              ) : (
                <MessageRow
                  m={m}
                  showHeader={showHeader}
                  accent={accent}
                  emoji={emoji}
                  deptNameById={deptNameById}
                  isLastAssistant={m.id === lastAssistantId}
                  onAction={onAction}
                  onFollowUp={pushSeed}
                  onOpenCitation={openCitation}
                />
              )}
            </div>
          )
        })}
      </div>

      {!atBottom && (
        <button
          type="button"
          onClick={() => {
            setAtBottom(true)
            scrollToBottom()
          }}
          className="absolute bottom-28 right-3 rounded-full px-3.5 py-1.5 text-[12px] font-semibold shadow-lg"
          style={{ background: withAlpha(ACCENT_HEX[accent], 0.95), color: '#1a1610' }}
        >
          ↓ {unseen > 0 ? `${unseen} ใหม่` : 'ล่าสุด'}
        </button>
      )}

      <Composer
        threadId={threadId}
        accent={accent}
        placeholder={placeholder}
        busy={busy}
        editing={editing}
        quoted={quoted}
        seed={seed}
        onSend={onSend}
        onStop={() => void client.stopGeneration(threadId)}
        onCancelContext={() => {
          setEditing(null)
          setQuoted(null)
        }}
        onEditLast={editLast}
      />
    </div>
  )
}
