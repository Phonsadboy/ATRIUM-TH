import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { withAlpha } from '../components/primitives'
import type { VersionStatusResponse, VersionUpdateResponse } from '../contract/types'
import { ACCENT_HEX } from '../lib/visuals'
import { client } from '../state/useCompany'

const VERSION_CHECK_INTERVAL_MS = 5 * 60 * 1000

function shouldShowVersionWarning(status: VersionStatusResponse): boolean {
  if (status.status === 'outdated' || status.status === 'diverged') return true
  return status.status === 'unknown' && Boolean(status.localCommit && status.remoteCommit)
}

function warningTitle(status: VersionStatusResponse): string {
  if (status.status === 'diverged') return 'เวอร์ชันในเครื่องแยกจาก GitHub'
  if (status.status === 'unknown') return 'เวอร์ชันในเครื่องไม่ตรงกับ GitHub'
  return 'เวอร์ชันในเครื่องเก่ากว่า GitHub'
}

export function VersionBanner() {
  const [status, setStatus] = useState<VersionStatusResponse | null>(null)
  const [updating, setUpdating] = useState(false)
  const [updateResult, setUpdateResult] = useState<VersionUpdateResponse | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const next = await client.getVersionStatus()
        if (active) {
          setStatus(next)
          if (next.status !== 'outdated') setUpdateResult(null)
        }
      } catch {
        if (active) setStatus(null)
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), VERSION_CHECK_INTERVAL_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  if (!status || (!shouldShowVersionWarning(status) && !updateResult)) return null

  const color = updateResult?.ok ? ACCENT_HEX.teal : status.status === 'diverged' ? ACCENT_HEX.coral : ACCENT_HEX.amber
  const local = status.localShort ?? 'unknown'
  const remote = status.remoteShort ?? 'unknown'
  const title = updateResult?.ok ? 'อัปเดตระบบแล้ว' : warningTitle(status)
  const canUpdate = status.status === 'outdated' && !status.dirty && !updating
  const disabledReason = status.dirty
    ? 'มีไฟล์แก้ค้าง'
    : status.status === 'diverged'
      ? 'ต้องตรวจ branch'
      : status.status === 'unknown'
        ? 'ตรวจไม่ได้'
        : ''

  const updateAndRestart = async () => {
    if (!canUpdate) return
    const confirmed = window.confirm(
      `อัปเดต ATRIUM จาก ${local} ไป ${remote}, backup, migrate แล้วรีสตาร์ทระบบทันที?\n\nระบบจะทำเฉพาะ git fast-forward และจะไม่ทำงานถ้ามีไฟล์แก้ค้าง`,
    )
    if (!confirmed) return
    setUpdating(true)
    setUpdateResult(null)
    try {
      const result = await client.updateVersion({ restart: true })
      setUpdateResult(result)
      if (result.after) setStatus(result.after)
    } catch {
      setUpdateResult({
        ok: false,
        status: 'failed',
        message: 'สั่งอัปเดตไม่สำเร็จ',
        before: status,
        after: status,
        restartScheduled: false,
        restartMode: 'manual',
      })
    } finally {
      setUpdating(false)
    }
  }

  return (
    <aside
      className="flex flex-col gap-2 border-b px-5 py-2 text-xs md:flex-row md:items-center"
      style={{
        borderColor: withAlpha(color, 0.38),
        background: `linear-gradient(90deg, ${withAlpha(color, 0.17)}, ${withAlpha(color, 0.06)})`,
        color: 'var(--color-cream)',
      }}
      role="status"
      aria-live="polite"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Icon name="alert" size={16} />
        <div className="min-w-0">
          <div className="font-semibold" style={{ color }}>
            {title}
          </div>
          <div className="truncate text-[11px] text-[var(--color-cream-dim)]">
            เครื่องนี้ {local} · GitHub {remote}
            {status.dirty ? ' · มีไฟล์ที่แก้ค้างอยู่' : ''}
          </div>
          {updateResult && (
            <div
              className="mt-0.5 truncate text-[11px]"
              style={{ color: updateResult.ok ? ACCENT_HEX.teal : ACCENT_HEX.coral }}
            >
              {updateResult.message}
            </div>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => void updateAndRestart()}
        disabled={!canUpdate}
        className="inline-flex shrink-0 items-center justify-center rounded-xl border px-3 py-1.5 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55"
        style={{
          borderColor: withAlpha(canUpdate ? ACCENT_HEX.teal : color, 0.45),
          color: canUpdate ? ACCENT_HEX.teal : 'var(--color-cream-dim)',
          background: withAlpha(canUpdate ? ACCENT_HEX.teal : color, 0.08),
        }}
      >
        {updating ? 'กำลังอัปเดต...' : canUpdate ? 'อัปเดต + migrate + รีสตาร์ท' : disabledReason}
      </button>
      {status.compareUrl && (
        <a
          href={status.compareUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex shrink-0 items-center justify-center rounded-xl border px-3 py-1.5 font-medium transition-colors hover:text-[var(--color-cream)]"
          style={{
            borderColor: withAlpha(color, 0.45),
            color,
            background: withAlpha(color, 0.08),
          }}
        >
          เปิด GitHub compare
        </a>
      )}
    </aside>
  )
}
