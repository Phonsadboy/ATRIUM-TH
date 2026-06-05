/* eslint-disable react-refresh/only-export-components */
// budgetColor is a shared helper exported alongside the TopBar component;
// RightDock + LeftRail import it, so it must live here (disables the
// react-refresh single-export rule for this file).
import { useEffect, useRef, useState } from 'react'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Dot, withAlpha } from '../components/primitives'
import { Logo, Icon, type IconName } from '../components/Icon'
import { ModeSwitch } from './ModeSwitch'
import { VersionStatusControl } from './VersionStatusControl'
import { clockSeconds, money } from '../lib/format'
import { ACCENT_HEX } from '../lib/visuals'
import { EXEC_ID } from '../lib/threads'
import { isHumanApproval } from '../lib/approvals'

function budgetColor(ratio: number): string {
  if (ratio >= 0.9) return ACCENT_HEX.coral
  if (ratio >= 0.6) return ACCENT_HEX.amber
  return ACCENT_HEX.teal
}

/** Thin vertical separator that visually groups the toolbar clusters. */
function Divider() {
  return <span className="h-5 w-px shrink-0" style={{ background: 'var(--color-line-soft)' }} />
}

/** Icon-only button with a corner count badge — used for the alert cluster. */
function AlertBtn({
  iconName,
  title,
  count,
  accent,
  onClick,
}: {
  iconName: IconName
  title: string
  count: number
  accent: string
  onClick: () => void
}) {
  const active = count > 0
  return (
    <button
      type="button"
      onClick={onClick}
      title={active ? `${title} (${count})` : title}
      aria-label={active ? `${title} (${count})` : title}
      className="relative inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors hover:text-[var(--color-cream)]"
      style={{
        color: active ? accent : 'var(--color-cream-dim)',
        background: active ? withAlpha(accent, 0.12) : 'transparent',
      }}
    >
      <Icon name={iconName} size={15} />
      {active && (
        <span
          className="absolute -top-1 -right-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-black"
          style={{ background: accent }}
        >
          {count}
        </span>
      )}
    </button>
  )
}

/** Row inside the ⋯ More dropdown. */
function MenuItem({
  iconName,
  label,
  onClick,
}: {
  iconName: IconName
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:bg-[var(--color-surface-4)] hover:text-[var(--color-cream)]"
    >
      <Icon name={iconName} size={15} />
      {label}
    </button>
  )
}

export function TopBar() {
  const now = useSelector((s) => s.now)
  const running = useSelector((s) => s.running)
  const budget = useSelector((s) => s.budget)
  const pending = useSelector(
    (s) => s.approvals.filter((a) => a.status === 'pending' && isHumanApproval(a)).length,
  )
  // exclude the executive so this matches LeftRail's แผนก ({rest.length}) count
  const deptCount = useSelector(
    (s) => s.departments.filter((d) => d.id !== EXEC_ID).length,
  )
  const toggleApprovals = useUI((s) => s.toggleApprovals)
  const toggleNotifications = useUI((s) => s.toggleNotifications)
  const toggleDecisions = useUI((s) => s.toggleDecisions)
  const toggleTaskBoard = useUI((s) => s.toggleTaskBoard)
  const openFinance = useUI((s) => s.openFinance)
  const openConsole = useUI((s) => s.openConsole)

  // the `now` selector above re-renders TopBar on every client bump, so this
  // plain getter read stays in sync with the notification inbox
  const unreadNotifs = client.getNotifications().filter((n) => !n.read).length

  const ratio = budget.dailyCapUsd ? budget.spentTodayUsd / budget.dailyCapUsd : 0
  const bcol = budgetColor(ratio)

  // ⋯ More dropdown holds the lower-traffic panels (task board / finance / log)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!moreOpen) return
    const onDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMoreOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMoreOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [moreOpen])

  return (
    <header
      className="flex items-center gap-2.5 px-5 py-2"
      style={{ borderBottom: '1px solid var(--color-line-soft)' }}
    >
      {/* brand + system status */}
      <div className="flex items-center gap-3">
        <Logo height={28} />
        <div className="leading-tight">
          <div className="text-[11px] text-[var(--color-cream-faint)]">
            บริษัท AI ที่ไม่เคยหลับ · {deptCount} แผนก
          </div>
        </div>
      </div>

      <Divider />

      <div className="flex items-center gap-2">
        <Dot color={running ? ACCENT_HEX.teal : ACCENT_HEX.coral} pulse={running} />
        <span
          className="text-sm tabular-nums"
          style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}
        >
          {clockSeconds(now)}
        </span>
        <span className="hidden text-[10px] tracking-widest text-[var(--color-cream-faint)] lg:inline">
          {running ? 'กำลังเดินระบบ' : 'หยุดชั่วคราว'}
        </span>
        <VersionStatusControl />
      </div>

      <div className="flex-1" />

      {/* budget meter — stays in place per the agreed layout */}
      <div className="hidden w-52 md:block">
        <div className="mb-1 flex items-baseline justify-between text-[11px]">
          <span className="text-[var(--color-cream-faint)]">งบวันนี้</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: bcol }}>
            {money(budget.spentTodayUsd)} / {money(budget.dailyCapUsd)}
          </span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full"
          style={{ background: 'var(--color-line-soft)' }}
        >
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${Math.min(100, ratio * 100)}%`,
              background: bcol,
              boxShadow: `0 0 10px ${withAlpha(bcol, 0.6)}`,
            }}
          />
        </div>
      </div>

      <Divider />

      {/* primary workspace */}
      <button
        type="button"
        onClick={() => openConsole()}
        className="flex h-8 items-center gap-1.5 rounded-xl border px-3 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="model" size={14} /> ศูนย์ปฏิบัติการ
      </button>

      {/* task board — labeled button (sits beside the workspace) */}
      <button
        type="button"
        onClick={() => toggleTaskBoard(true)}
        className="flex h-8 items-center gap-1.5 rounded-xl border px-3 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="tasks" size={14} /> บอร์ดงาน
      </button>

      {/* alert cluster — notifications · approvals, one icon+badge group */}
      <div
        className="flex items-center gap-0.5 rounded-xl border p-0.5"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <AlertBtn
          iconName="alert"
          title="แจ้งเตือน"
          count={unreadNotifs}
          accent={ACCENT_HEX.amber}
          onClick={() => toggleNotifications(true)}
        />
        <AlertBtn
          iconName="approve"
          title="อนุมัติ"
          count={pending}
          accent={ACCENT_HEX.coral}
          onClick={() => toggleApprovals(true)}
        />
      </div>

      {/* ⋯ More — task board / finance / decision log */}
      <div className="relative" ref={moreRef}>
        <button
          type="button"
          onClick={() => setMoreOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={moreOpen}
          title="เพิ่มเติม"
          className="flex h-8 w-9 items-center justify-center rounded-xl border text-base leading-none text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
          style={{
            borderColor: moreOpen ? withAlpha(ACCENT_HEX.amber, 0.4) : 'var(--color-line-soft)',
            background: moreOpen ? withAlpha(ACCENT_HEX.amber, 0.1) : 'transparent',
            color: moreOpen ? ACCENT_HEX.amber : undefined,
          }}
        >
          ⋯
        </button>
        {moreOpen && (
          <div
            role="menu"
            className="panel absolute right-0 z-50 w-52 p-1.5"
            style={{ top: 'calc(100% + 8px)', borderRadius: 14 }}
          >
            <MenuItem
              iconName="budget"
              label="การเงิน"
              onClick={() => {
                openFinance()
                setMoreOpen(false)
              }}
            />
            <MenuItem
              iconName="archive"
              label="บันทึกการตัดสินใจ"
              onClick={() => {
                toggleDecisions(true)
                setMoreOpen(false)
              }}
            />
          </div>
        )}
      </div>

      <Divider />

      {/* AI permission mode (compact) */}
      <ModeSwitch />

      {/* kill switch */}
      <button
        type="button"
        onClick={() => client.setRunning(!running)}
        className="flex h-8 items-center gap-2 rounded-xl px-3 text-xs font-semibold transition-colors"
        style={{
          background: running ? withAlpha(ACCENT_HEX.coral, 0.14) : withAlpha(ACCENT_HEX.teal, 0.16),
          color: running ? ACCENT_HEX.coral : ACCENT_HEX.teal,
          border: `1px solid ${running ? withAlpha(ACCENT_HEX.coral, 0.4) : withAlpha(ACCENT_HEX.teal, 0.4)}`,
        }}
      >
        {running ? (
          <>
            <Icon name="power" size={14} /> หยุดทั้งระบบ
          </>
        ) : (
          <>
            <Icon name="play" size={14} /> เริ่มระบบ
          </>
        )}
      </button>
    </header>
  )
}

export { budgetColor }
