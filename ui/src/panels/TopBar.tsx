/* eslint-disable react-refresh/only-export-components */
// budgetColor is a shared helper exported alongside the TopBar component;
// RightDock + LeftRail import it, so it must live here (disables the
// react-refresh single-export rule for this file).
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Dot, withAlpha } from '../components/primitives'
import { Logo, Icon } from '../components/Icon'
import { ModeSwitch } from './ModeSwitch'
import { clockSeconds, money } from '../lib/format'
import { ACCENT_HEX } from '../lib/visuals'
import { EXEC_ID } from '../lib/threads'
import { isHumanApproval } from '../lib/approvals'

function budgetColor(ratio: number): string {
  if (ratio >= 0.9) return ACCENT_HEX.coral
  if (ratio >= 0.6) return ACCENT_HEX.amber
  return ACCENT_HEX.teal
}

export function TopBar() {
  const now = useSelector((s) => s.now)
  const running = useSelector((s) => s.running)
  const budget = useSelector((s) => s.budget)
  const pending = useSelector(
    (s) => s.approvals.filter((a) => a.status === 'pending' && isHumanApproval(a)).length,
  )
  const queueCount = useSelector(
    (s) => s.executiveQueue.filter((item) => item.status === 'queued' || item.status === 'running').length,
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
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)

  // the `now` selector above re-renders TopBar on every client bump, so this
  // plain getter read stays in sync with the notification inbox
  const unreadNotifs = client.getNotifications().filter((n) => !n.read).length

  const ratio = budget.dailyCapUsd ? budget.spentTodayUsd / budget.dailyCapUsd : 0
  const bcol = budgetColor(ratio)

  return (
    <header
      className="flex items-center gap-3 px-5 py-2"
      style={{ borderBottom: '1px solid var(--color-line-soft)' }}
    >
      {/* brand */}
      <div className="flex items-center gap-3">
        <Logo height={28} />
        <div className="leading-tight">
          <div className="text-[11px] text-[var(--color-cream-faint)]">
            บริษัท AI ที่ไม่เคยหลับ · {deptCount} แผนก
          </div>
        </div>
      </div>

      {/* live clock */}
      <div className="ml-2 flex items-center gap-2">
        <Dot color={running ? ACCENT_HEX.teal : ACCENT_HEX.coral} pulse={running} />
        <span
          className="text-sm tabular-nums"
          style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-cream-dim)' }}
        >
          {clockSeconds(now)}
        </span>
        <span className="text-[10px] tracking-widest text-[var(--color-cream-faint)]">
          {running ? 'กำลังเดินระบบ' : 'หยุดชั่วคราว'}
        </span>
      </div>

      <div className="flex-1" />

      {/* budget meter */}
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

      {/* operations console */}
      <button
        type="button"
        onClick={() => openConsole()}
        className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="model" size={14} /> ศูนย์ปฏิบัติการ
      </button>

      {/* task board */}
      <button
        type="button"
        onClick={() => toggleTaskBoard(true)}
        className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="tasks" size={14} /> บอร์ดงาน
      </button>

      {/* finance */}
      <button
        type="button"
        onClick={() => openFinance()}
        className="flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="budget" size={14} /> การเงิน
      </button>

      {/* decision log */}
      <button
        type="button"
        onClick={() => toggleDecisions(true)}
        title="บันทึกการตัดสินใจ"
        className="flex items-center justify-center rounded-xl border px-2.5 py-1.5 text-xs font-medium text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        <Icon name="archive" size={14} />
      </button>

      {/* executive queue */}
      <button
        type="button"
        onClick={() => {
          select(EXEC_ID)
          setRightTab('watch')
        }}
        className="relative flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors"
        style={{
          borderColor: queueCount
            ? withAlpha(ACCENT_HEX.lavender, 0.45)
            : 'var(--color-line-soft)',
          color: queueCount ? ACCENT_HEX.lavender : 'var(--color-cream-dim)',
          background: queueCount ? withAlpha(ACCENT_HEX.lavender, 0.1) : 'transparent',
        }}
      >
        <Icon name="tasks" size={14} /> คิว
        {queueCount > 0 && (
          <span
            className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-black"
            style={{ background: ACCENT_HEX.lavender }}
          >
            {queueCount}
          </span>
        )}
      </button>

      {/* notifications */}
      <button
        type="button"
        onClick={() => toggleNotifications(true)}
        title="แจ้งเตือน"
        className="relative flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors"
        style={{
          borderColor: unreadNotifs
            ? withAlpha(ACCENT_HEX.amber, 0.45)
            : 'var(--color-line-soft)',
          color: unreadNotifs ? ACCENT_HEX.amber : 'var(--color-cream-dim)',
          background: unreadNotifs ? withAlpha(ACCENT_HEX.amber, 0.1) : 'transparent',
        }}
      >
        <Icon name="alert" size={14} />
        {unreadNotifs > 0 && (
          <span
            className="absolute -top-1.5 -right-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-black"
            style={{ background: ACCENT_HEX.amber }}
          >
            {unreadNotifs}
          </span>
        )}
      </button>

      {/* approvals */}
      <button
        type="button"
        onClick={() => toggleApprovals(true)}
        className="relative flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors"
        style={{
          borderColor: pending
            ? withAlpha(ACCENT_HEX.coral, 0.45)
            : 'var(--color-line-soft)',
          color: pending ? ACCENT_HEX.coral : 'var(--color-cream-dim)',
          background: pending ? withAlpha(ACCENT_HEX.coral, 0.1) : 'transparent',
        }}
      >
        <Icon name="approve" size={14} /> อนุมัติ
        {pending > 0 && (
          <span
            className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-black"
            style={{ background: ACCENT_HEX.coral }}
          >
            {pending}
          </span>
        )}
      </button>

      {/* AI permission mode — how often the company pauses for your approval */}
      <ModeSwitch />

      {/* kill switch */}
      <button
        type="button"
        onClick={() => client.setRunning(!running)}
        className="flex items-center gap-2 rounded-xl px-3 py-1.5 text-xs font-semibold transition-colors"
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
