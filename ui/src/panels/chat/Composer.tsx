import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { Icon } from '../../components/Icon'
import { withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { client } from '../../state/useCompany'
import type {
  AccentName,
  Artifact,
  ChatMessage,
  InputEstimate,
  MessageAttachment,
  MessageMentionTarget,
  ThinkingEffort,
} from '../../contract/types'

const SLASH = [
  { name: 'assign', desc: 'มอบหมายงานให้ฝ่าย', usage: '/assign <ฝ่าย> <งาน>' },
  { name: 'summarize', desc: 'สรุปบทสนทนานี้', usage: '/summarize' },
  { name: 'cost', desc: 'ค่าใช้จ่ายของเธรดนี้', usage: '/cost' },
  { name: 'memory', desc: 'ค้นความจำของฝ่าย', usage: '/memory <คำค้น>' },
  { name: 'clear', desc: 'ล้างบทสนทนา', usage: '/clear' },
  { name: 'help', desc: 'ดูคำสั่งทั้งหมด', usage: '/help' },
]

function attachmentFromArtifact(artifact: Artifact): MessageAttachment {
  return {
    artifactId: artifact.id,
    name: artifact.name,
    kind: artifact.kind,
    mime: artifact.mime ?? artifact.contentMime ?? null,
    uri: artifact.uri,
    sizeBytes: artifact.contentSizeBytes ?? null,
  }
}

function fileSizeLabel(bytes?: number | null): string {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

export function Composer({
  threadId,
  accent,
  placeholder,
  busy,
  editing,
  quoted,
  seed,
  onSend,
  onStop,
  onCancelContext,
  onEditLast,
}: {
  threadId: string
  accent: AccentName
  placeholder: string
  busy: boolean
  editing?: ChatMessage | null
  quoted?: ChatMessage | null
  seed?: { text: string; nonce: number }
  onSend: (text: string, effort: ThinkingEffort | '', attachments: MessageAttachment[]) => void
  onStop: () => void
  onCancelContext: () => void
  onEditLast: () => void
}) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<MessageAttachment[]>([])
  const [uploading, setUploading] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [estimate, setEstimate] = useState<InputEstimate | null>(null)
  const [mentions, setMentions] = useState<MessageMentionTarget[] | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const dragDepth = useRef(0)
  const skipSave = useRef(true)
  const hex = ACCENT_HEX[accent]

  // Load this thread's server-side draft on mount (Composer remounts per thread).
  useEffect(() => {
    let live = true
    skipSave.current = true
    client
      .getDraft(threadId)
      .then((d) => {
        if (!live) return
        if (d?.text) setText(d.text)
        if (d?.attachments?.length) setAttachments(d.attachments)
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [threadId])

  // Push externally-seeded text (follow-up chip, edit, quote) into the box.
  useEffect(() => {
    if (!seed) return
    const id = requestAnimationFrame(() => {
      setText(seed.text)
      taRef.current?.focus()
    })
    return () => cancelAnimationFrame(id)
  }, [seed])

  // Auto-grow the textarea with its content, up to a cap (then it scrolls).
  const autosize = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = '0px'
    ta.style.height = `${Math.min(Math.max(ta.scrollHeight, 40), 240)}px`
  }, [])

  // Re-measure on content change…
  useEffect(() => {
    autosize()
  }, [text, autosize])

  // …and when the textarea's width changes (panel mount/expand or drag-resize):
  // a wrapped placeholder or long line would otherwise leave a stale height.
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    const ro = new ResizeObserver(() => autosize())
    ro.observe(ta)
    return () => ro.disconnect()
  }, [autosize])

  // Debounced draft autosave + token estimate.
  useEffect(() => {
    if (skipSave.current) {
      skipSave.current = false
      return
    }
    const t = window.setTimeout(() => {
      void client.putDraft(threadId, { text, attachments }).catch(() => undefined)
      if (text.trim() || attachments.length) {
        client
          .estimateInput(threadId, { text, attachments })
          .then(setEstimate)
          .catch(() => undefined)
      } else {
        setEstimate(null)
      }
    }, 500)
    return () => window.clearTimeout(t)
  }, [text, attachments, threadId])

  // @mention autocomplete — detect a trailing @token under the caret.
  useEffect(() => {
    const m = /(?:^|\s)@([\p{L}\w-]*)$/u.exec(text)
    const t = window.setTimeout(() => {
      if (!m) {
        setMentions(null)
        return
      }
      client
        .getMentions(threadId, m[1], 6)
        .then((list) => setMentions(list.length ? list : null))
        .catch(() => setMentions(null))
    }, 180)
    return () => window.clearTimeout(t)
  }, [text, threadId])

  const slashQuery = /^\/(\w*)$/.exec(text)
  const slashMatches = slashQuery
    ? SLASH.filter((s) => s.name.startsWith(slashQuery[1].toLowerCase()))
    : []

  const send = () => {
    const t = text.trim()
    // The backend accepts queued chat turns, so keep command input available
    // while the current reply is still running.
    if (!t && attachments.length === 0) return
    onSend(t, '', attachments)
    setText('')
    setAttachments([])
    setEstimate(null)
    setMentions(null)
    void client.deleteDraft(threadId).catch(() => undefined)
  }

  const addFiles = (files: FileList | null) => {
    if (!files?.length) return
    setUploadError(null)
    const selected = Array.from(files)
    setUploading((n) => n + selected.length)
    selected.forEach((file) => {
      client
        .uploadAttachment(threadId, file, { artifactName: file.name })
        .then((res) => {
          const next = attachmentFromArtifact(res.artifact)
          setAttachments((prev) =>
            prev.some((a) => a.artifactId === next.artifactId) ? prev : [...prev, next],
          )
        })
        .catch(() => setUploadError(`อัปโหลด ${file.name} ไม่สำเร็จ`))
        .finally(() => setUploading((n) => Math.max(0, n - 1)))
    })
    if (fileRef.current) fileRef.current.value = ''
  }

  const dragHasFiles = (event: DragEvent<HTMLDivElement>) => Array.from(event.dataTransfer.types).includes('Files')

  const onDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    dragDepth.current += 1
    setDragActive(true)
  }

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = 'copy'
    setDragActive(true)
  }

  const onDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragActive(false)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(event)) return
    event.preventDefault()
    event.stopPropagation()
    dragDepth.current = 0
    setDragActive(false)
    addFiles(event.dataTransfer.files)
  }

  const removeAttachment = (artifactId?: string | null) => {
    if (!artifactId) return
    setAttachments((prev) => prev.filter((item) => item.artifactId !== artifactId))
  }

  const pickMention = (mt: MessageMentionTarget) => {
    const label = mt.displayName || mt.agentName || mt.raw
    setText((prev) => prev.replace(/(?:^|\s)@([\p{L}\w-]*)$/u, (full) => full.replace(/@[\p{L}\w-]*$/u, `@${label} `)))
    setMentions(null)
    taRef.current?.focus()
  }

  const pickSlash = (name: string) => {
    setText(`/${name} `)
    taRef.current?.focus()
  }

  const warn = estimate?.warnings?.length ? estimate.warnings[0] : null

  return (
    <div
      className="relative mt-1.5 rounded-2xl"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      aria-label="พื้นที่แชตสำหรับแนบไฟล์"
    >
      {dragActive && (
        <div
          className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl text-[13px] font-semibold"
          style={{
            background: withAlpha(hex, 0.16),
            border: `1px dashed ${withAlpha(hex, 0.62)}`,
            color: 'var(--color-cream)',
          }}
        >
          วางไฟล์
        </div>
      )}
      {/* mention dropdown */}
      {mentions && (
        <div
          className="absolute bottom-full mb-1 w-full overflow-hidden rounded-xl"
          style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-line-soft)' }}
        >
          {mentions.map((mt) => (
            <button
              key={mt.raw + (mt.departmentId ?? '')}
              type="button"
              onClick={() => pickMention(mt)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-[var(--color-surface)]"
            >
              <span style={{ color: hex }}>@</span>
              <span className="font-medium">{mt.displayName || mt.agentName}</span>
              {mt.matchedBy && <span className="text-[11px] text-[var(--color-cream-faint)]">{mt.matchedBy}</span>}
            </button>
          ))}
        </div>
      )}

      {/* slash palette */}
      {slashMatches.length > 0 && (
        <div
          className="absolute bottom-full mb-1 w-full overflow-hidden rounded-xl"
          style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-line-soft)' }}
        >
          {slashMatches.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => pickSlash(s.name)}
              className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-[var(--color-surface)]"
            >
              <span className="text-[13px] font-semibold" style={{ color: hex }}>/{s.name}</span>
              <span className="text-[12px] text-[var(--color-cream-faint)]">{s.desc}</span>
              <span className="ml-auto text-[10px] text-[var(--color-cream-faint)]">{s.usage}</span>
            </button>
          ))}
        </div>
      )}

      {/* quote / edit context banner */}
      {(quoted || editing) && (
        <div
          className="mb-1.5 flex items-center gap-2 rounded-lg px-3 py-1.5 text-[12px]"
          style={{ background: withAlpha(hex, 0.1), border: `1px solid ${withAlpha(hex, 0.3)}` }}
        >
          <span style={{ color: hex }}>{editing ? '✎ กำลังแก้ไข & แตกสาขา' : '❝ กำลังอ้างถึง'}</span>
          <span className="flex-1 truncate text-[var(--color-cream-faint)]">
            {(editing ?? quoted)?.text}
          </span>
          <button type="button" onClick={onCancelContext} className="text-[var(--color-cream-faint)] hover:opacity-70">
            ✕
          </button>
        </div>
      )}

      {(attachments.length > 0 || uploading > 0 || uploadError) && (
        <div className="mb-1.5 flex max-h-24 flex-wrap gap-1.5 overflow-y-auto px-0.5">
          {attachments.map((item) => (
            <span
              key={item.artifactId ?? item.uri ?? item.name}
              className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-[12px]"
              style={{ background: withAlpha(hex, 0.1), border: `1px solid ${withAlpha(hex, 0.28)}` }}
              title={item.mime ?? item.kind ?? 'attachment'}
            >
              <span className="shrink-0" style={{ color: hex }}>↗</span>
              <span className="min-w-0 truncate">{item.name ?? item.artifactId ?? 'ไฟล์แนบ'}</span>
              {item.sizeBytes ? <span className="shrink-0 text-[10px] text-[var(--color-cream-faint)]">{fileSizeLabel(item.sizeBytes)}</span> : null}
              <button
                type="button"
                onClick={() => removeAttachment(item.artifactId)}
                className="shrink-0 text-[var(--color-cream-faint)] hover:text-[var(--color-cream)]"
                title="เอาไฟล์ออก"
              >
                x
              </button>
            </span>
          ))}
          {uploading > 0 && (
            <span className="rounded-lg px-2 py-1 text-[12px] text-[var(--color-cream-faint)]" style={{ border: '1px solid var(--color-line-soft)' }}>
              กำลังอัปโหลด {uploading}
            </span>
          )}
          {uploadError && <span className="px-1 py-1 text-[12px]" style={{ color: '#f0735f' }}>{uploadError}</span>}
        </div>
      )}

      <div
        className="flex flex-col gap-1.5 rounded-2xl border p-2 transition-colors focus-within:border-[var(--color-line)]"
        style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface)' }}
      >
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          aria-label="แนบไฟล์"
          onChange={(e) => addFiles(e.currentTarget.files)}
        />

        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            } else if (e.key === 'Escape') {
              if (mentions || slashMatches.length) {
                setMentions(null)
                setText((t) => t)
              } else if (editing || quoted) {
                onCancelContext()
              }
            } else if (e.key === 'ArrowUp' && !text && !editing) {
              e.preventDefault()
              onEditLast()
            }
          }}
          rows={1}
          placeholder={placeholder}
          className="w-full resize-none overflow-y-auto bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-[var(--color-cream)] outline-none placeholder:text-[var(--color-cream-faint)]"
          style={{ minHeight: 40, maxHeight: 240 }}
        />

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--color-cream-faint)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-cream-dim)]"
            title="แนบไฟล์"
            aria-label="แนบไฟล์"
          >
            <Icon name="plus" size={14} />
          </button>

          {/* live counter fills the otherwise-empty middle of the action bar */}
          <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden text-[11px] text-[var(--color-cream-faint)]">
            {(text.length > 0 || attachments.length > 0) && <span className="shrink-0">{text.length} ตัวอักษร</span>}
            {attachments.length > 0 && <span className="shrink-0">· {attachments.length} ไฟล์</span>}
            {estimate && <span className="shrink-0">· ~{estimate.estimatedTokens.toLocaleString()} โทเคน</span>}
            {warn && <span className="truncate" style={{ color: '#f4a945' }}>· ⚠️ {warn}</span>}
          </div>

          {busy && !editing && (
            <button
              type="button"
              onClick={onStop}
              className="shrink-0 rounded-xl px-3 py-2 text-sm font-semibold"
              style={{ background: withAlpha('#f0735f', 0.9), color: '#1a1610' }}
              title="หยุดคำตอบที่กำลังสร้าง"
            >
              ■
            </button>
          )}
          <button
            type="button"
            onClick={send}
            disabled={(!text.trim() && attachments.length === 0) || uploading > 0}
            className="shrink-0 rounded-xl px-4 py-2 text-sm font-semibold transition-opacity disabled:opacity-40"
            style={{ background: withAlpha(hex, 0.9), color: '#1a1610' }}
            title="Enter ส่ง/เข้าคิว · Shift+Enter ขึ้นบรรทัด"
          >
            {editing ? 'บันทึก' : busy ? 'ส่งเข้าคิว' : 'ส่ง'}
          </button>
        </div>
      </div>
    </div>
  )
}
