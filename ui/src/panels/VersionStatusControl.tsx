import { useCallback, useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { withAlpha } from '../components/primitives'
import type { VersionStatusResponse, VersionUpdateResponse } from '../contract/types'
import { ACCENT_HEX } from '../lib/visuals'
import { client } from '../state/useCompany'

const VERSION_CHECK_INTERVAL_MS = 5 * 60 * 1000

function isWarning(status: VersionStatusResponse): boolean {
  return status.status === 'outdated' || status.status === 'diverged' || status.status === 'unknown'
}

function compactLabel(status: VersionStatusResponse, loadError: boolean, updateOk: boolean): string {
  if (loadError) return 'ตรวจเวอร์ชันไม่ได้'
  if (updateOk) return 'อัปเดตแล้ว'
  if (status.status === 'outdated') return 'เวอร์ชันเก่า'
  if (status.status === 'diverged') return 'branch แยก'
  if (status.status === 'ahead') return 'ใหม่กว่า GitHub'
  if (status.status === 'unknown') return 'ตรวจไม่ชัด'
  return 'ตรวจเวอร์ชัน'
}

function compactColor(status: VersionStatusResponse | null, loadError: boolean, updateOk: boolean): string {
  if (loadError) return ACCENT_HEX.amber
  if (updateOk) return ACCENT_HEX.teal
  if (!status) return 'var(--color-cream-dim)'
  if (status.status === 'outdated' || status.status === 'unknown' || status.dirty) return ACCENT_HEX.amber
  if (status.status === 'diverged') return ACCENT_HEX.coral
  if (status.status === 'ahead') return ACCENT_HEX.lavender
  return 'var(--color-cream-dim)'
}

function detailText(status: VersionStatusResponse | null, loadError: boolean): string {
  if (loadError) return 'restart backend เพื่อเปิด /api/version'
  if (!status) return 'กำลังตรวจ'
  const local = status.localShort ?? 'unknown'
  const remote = status.remoteShort ?? 'unknown'
  if (status.status === 'current' && status.dirty) return `${local} ตรง GitHub, มีไฟล์แก้ค้าง`
  if (status.status === 'current') return `${local} ตรง GitHub`
  if (status.status === 'outdated') return `เครื่องนี้ ${local}, GitHub ${remote}`
  if (status.status === 'ahead') return `${local} ยังใหม่กว่า remote`
  if (status.status === 'diverged') return `เครื่องนี้ ${local}, GitHub ${remote}`
  return status.error || `เครื่องนี้ ${local}, GitHub ${remote}`
}

export function VersionStatusControl() {
  const [status, setStatus] = useState<VersionStatusResponse | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [checking, setChecking] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [updateResult, setUpdateResult] = useState<VersionUpdateResponse | null>(null)

  const load = useCallback(async (isActive: () => boolean = () => true) => {
    setChecking(true)
    try {
      const next = await client.getVersionStatus()
      if (!isActive()) return
      setStatus(next)
      setLoadError(false)
      if (next.status !== 'outdated') setUpdateResult(null)
    } catch {
      if (!isActive()) return
      setStatus(null)
      setLoadError(true)
    } finally {
      if (isActive()) setChecking(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    const loadActive = async () => {
      await load(() => active)
    }
    void loadActive()
    const timer = window.setInterval(() => void loadActive(), VERSION_CHECK_INTERVAL_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [load])

  const canUpdate = Boolean(status && status.status === 'outdated' && !status.dirty && !updating)
  const warning = Boolean(status && isWarning(status)) || loadError
  const updateOk = Boolean(updateResult?.ok)
  const color = compactColor(status, loadError, updateOk)
  const label = status ? compactLabel(status, loadError, updateOk) : checking ? 'กำลังตรวจ' : 'ตรวจเวอร์ชัน'
  const detail = updateResult?.message ?? detailText(status, loadError)

  const updateAndRestart = async () => {
    if (!status || !canUpdate) return
    const local = status.localShort ?? 'unknown'
    const remote = status.remoteShort ?? 'unknown'
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
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => (canUpdate ? void updateAndRestart() : void load())}
        title={detail}
        className="inline-flex h-7 max-w-[180px] items-center gap-1.5 rounded-xl border px-2 text-[10px] font-medium transition-colors hover:text-[var(--color-cream)]"
        style={{
          borderColor: warning ? withAlpha(color, 0.5) : 'var(--color-line-soft)',
          color,
          background: warning ? withAlpha(color, 0.12) : 'transparent',
        }}
      >
        <Icon name={warning ? 'alert' : 'clock'} size={12} />
        <span className="truncate">
          {updating ? 'กำลังอัปเดต' : label}
        </span>
      </button>
      {canUpdate && (
        <a
          href={status?.compareUrl ?? '#'}
          target="_blank"
          rel="noreferrer"
          title="เปิด GitHub compare"
          className="hidden h-7 items-center rounded-xl border px-2 text-[10px] font-medium md:inline-flex"
          style={{
            borderColor: withAlpha(ACCENT_HEX.amber, 0.45),
            color: ACCENT_HEX.amber,
            background: withAlpha(ACCENT_HEX.amber, 0.08),
          }}
        >
          compare
        </a>
      )}
    </div>
  )
}
