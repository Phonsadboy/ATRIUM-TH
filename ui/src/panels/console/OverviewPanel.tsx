// The Console home. A glanceable picture of the company plus the short list of
// things that actually need an owner decision — pulled live from useAttention.
import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import { useSelector } from '../../state/useCompany'
import { useUI } from '../../state/ui'
import type { ConsoleSection } from '../../state/ui'
import { isExec } from '../../lib/threads'
import { money, clockSeconds } from '../../lib/format'
import { ACCENT_HEX } from '../../lib/visuals'
import { withAlpha } from '../../components/primitives'
import { Section, StatTile } from './shared'
import { SECTION_BY_ID } from './sections'
import type { Attention } from './useAttention'

const WORKING_STATES = new Set(['working', 'thinking', 'review', 'handoff'])
const ACTIVE_TASK = new Set(['assigned', 'in_progress', 'review', 'revising'])
const QUICK: ConsoleSection[] = ['projects', 'artifacts', 'tools', 'meetings', 'memory', 'audit', 'triggers', 'onboarding']

function budgetHex(ratio: number): string {
  if (ratio >= 0.9) return ACCENT_HEX.coral
  if (ratio >= 0.6) return ACCENT_HEX.amber
  return ACCENT_HEX.teal
}

interface AttnCard {
  key: string
  glyph: string
  accent: string
  label: string
  sub?: string
  count: number
  onClick: () => void
}

export function OverviewPanel({ attention }: { attention: Attention }) {
  const companyName = useSelector((s) => s.companyName)
  const running = useSelector((s) => s.running)
  const now = useSelector((s) => s.now)
  const dailyCap = useSelector((s) => s.budget.dailyCapUsd)
  const spentToday = useSelector((s) => s.budget.spentTodayUsd)
  const deptTotal = useSelector((s) => s.departments.filter((d) => !isExec(d.id)).length)
  const deptWorking = useSelector(
    (s) => s.departments.filter((d) => !isExec(d.id) && WORKING_STATES.has(d.state)).length,
  )
  const activeTasks = useSelector((s) => s.tasks.filter((t) => ACTIVE_TASK.has(t.status)).length)
  const blockedTasks = useSelector((s) => s.tasks.filter((t) => t.status === 'blocked').length)

  const setSection = useUI((s) => s.setConsoleSection)
  const closeConsole = useUI((s) => s.closeConsole)
  const toggleApprovals = useUI((s) => s.toggleApprovals)

  const ratio = dailyCap ? spentToday / dailyCap : 0
  const bcol = budgetHex(ratio)

  const go = (id: ConsoleSection) => setSection(id)

  const cards: AttnCard[] = []
  if (attention.approvals.length)
    cards.push({
      key: 'approvals',
      glyph: '🔔',
      accent: ACCENT_HEX.coral,
      label: 'คำขออนุมัติรอการตัดสินใจ',
      sub: attention.approvals[0]?.title,
      count: attention.approvals.length,
      onClick: () => {
        closeConsole()
        toggleApprovals(true)
      },
    })
  if (attention.projectReviews.length)
    cards.push({
      key: 'projectReviews',
      glyph: '📋',
      accent: ACCENT_HEX.teal,
      label: 'โปรเจกต์รอคุณเซ็นอนุมัติส่งมอบ',
      sub: attention.projectReviews[0]?.name,
      count: attention.projectReviews.length,
      onClick: () => go('projects'),
    })
  if (attention.toolApprovals.length)
    cards.push({
      key: 'toolApprovals',
      glyph: '🧰',
      accent: ACCENT_HEX.lavender,
      label: 'การใช้เครื่องมือรอการอนุมัติ',
      count: attention.toolApprovals.length,
      onClick: () => go('tools'),
    })
  if (attention.orgPlans.length)
    cards.push({
      key: 'orgPlans',
      glyph: '🏛️',
      accent: ACCENT_HEX.amber,
      label: 'แผนโครงสร้างองค์กรที่เสนอมา',
      sub: attention.orgPlans[0]?.objective,
      count: attention.orgPlans.length,
      onClick: () => go('onboarding'),
    })
  if (attention.warRooms.length)
    cards.push({
      key: 'warRooms',
      glyph: '🚨',
      accent: ACCENT_HEX.coral,
      label: 'วอร์รูมที่กำลังเปิดอยู่',
      sub: attention.warRooms[0]?.title,
      count: attention.warRooms.length,
      onClick: () => go('warrooms'),
    })
  if (attention.debt.length)
    cards.push({
      key: 'debt',
      glyph: '📉',
      accent: ACCENT_HEX.honey,
      label: 'แผนกที่หนี้ความรู้เริ่มสูง',
      count: attention.debt.length,
      onClick: () => go('debt'),
    })

  return (
    <Section>
      <div className="space-y-5 pb-2">
        {/* hero */}
        <Block delay={0}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3
                className="text-[22px] leading-tight"
                style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
              >
                {companyName}
              </h3>
              <div className="mt-1 flex items-center gap-2 text-[12px] text-[var(--color-cream-dim)]">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{
                    background: running ? ACCENT_HEX.teal : ACCENT_HEX.coral,
                    boxShadow: `0 0 8px ${running ? ACCENT_HEX.teal : ACCENT_HEX.coral}`,
                  }}
                />
                {running ? 'บริษัทกำลังเดินระบบ' : 'หยุดทั้งระบบชั่วคราว'}
                <span className="text-[var(--color-cream-faint)]">·</span>
                <span className="tabular-nums" style={{ fontFamily: 'var(--font-mono)' }}>
                  {clockSeconds(now)}
                </span>
              </div>
            </div>
          </div>
        </Block>

        {/* pulse tiles */}
        <Block delay={0.04}>
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            <StatTile
              label="งบวันนี้"
              accent={bcol}
              value={money(spentToday)}
              sub={
                <span className="flex flex-col gap-1">
                  <span className="text-[var(--color-cream-faint)]">จาก {money(dailyCap)}</span>
                  <span
                    className="h-1.5 w-full overflow-hidden rounded-full"
                    style={{ background: 'var(--color-line-soft)' }}
                  >
                    <span
                      className="block h-full rounded-full"
                      style={{ width: `${Math.min(100, ratio * 100)}%`, background: bcol }}
                    />
                  </span>
                </span>
              }
            />
            <StatTile
              label="แผนกที่ตื่นอยู่"
              accent={ACCENT_HEX.teal}
              value={
                <>
                  {deptWorking}
                  <span className="text-[14px] text-[var(--color-cream-faint)]"> / {deptTotal}</span>
                </>
              }
              sub="กำลังทำงานอยู่ตอนนี้"
            />
            <StatTile
              label="งานที่กำลังเดิน"
              accent={ACCENT_HEX.sky}
              value={activeTasks}
              sub={blockedTasks > 0 ? `ติดปัญหา ${blockedTasks} งาน` : 'ไม่มีงานติดปัญหา'}
            />
            <StatTile
              label="รอคุณตัดสินใจ"
              accent={attention.decisions > 0 ? ACCENT_HEX.coral : ACCENT_HEX.teal}
              value={attention.decisions}
              sub={attention.decisions > 0 ? 'ดูรายการด้านล่าง' : 'เคลียร์หมดแล้ว'}
            />
          </div>
        </Block>

        {/* needs attention */}
        <Block delay={0.08}>
          <div className="mb-2 flex items-center gap-2">
            <h4
              className="text-[13px]"
              style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
            >
              สิ่งที่รอคุณตัดสินใจ
            </h4>
            {cards.length > 0 && (
              <span
                className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-bold"
                style={{ background: withAlpha(ACCENT_HEX.coral, 0.16), color: ACCENT_HEX.coral }}
              >
                {cards.reduce((n, c) => n + c.count, 0)}
              </span>
            )}
          </div>
          {cards.length === 0 ? (
            <div
              className="flex items-center gap-3 rounded-[14px] border px-4 py-5"
              style={{ borderColor: withAlpha(ACCENT_HEX.teal, 0.25), background: withAlpha(ACCENT_HEX.teal, 0.06) }}
            >
              <span className="text-2xl">✅</span>
              <div>
                <div className="text-[13px] font-medium text-[var(--color-cream)]">ทุกอย่างเรียบร้อย</div>
                <div className="text-[11.5px] text-[var(--color-cream-dim)]">
                  ไม่มีอะไรค้างรอการตัดสินใจของคุณ ปล่อยให้ทีมเดินงานต่อได้เลย
                </div>
              </div>
            </div>
          ) : (
            <div className="grid gap-2.5 sm:grid-cols-2">
              {cards.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  onClick={c.onClick}
                  className="group/attn relative flex items-center gap-3 overflow-hidden border p-3.5 text-left transition-all hover:-translate-y-px"
                  style={{
                    borderRadius: 14,
                    borderColor: withAlpha(c.accent, 0.3),
                    background: `linear-gradient(150deg, ${withAlpha(c.accent, 0.1)}, var(--color-surface-2))`,
                  }}
                >
                  <span
                    className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg"
                    style={{ background: withAlpha(c.accent, 0.16), border: `1px solid ${withAlpha(c.accent, 0.3)}` }}
                  >
                    {c.glyph}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium text-[var(--color-cream)]">{c.label}</div>
                    {c.sub && (
                      <div className="truncate text-[11px] text-[var(--color-cream-dim)]">{c.sub}</div>
                    )}
                  </div>
                  <span
                    className="inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-full px-1.5 text-[12px] font-bold tabular-nums"
                    style={{ background: withAlpha(c.accent, 0.2), color: c.accent }}
                  >
                    {c.count}
                  </span>
                  <span
                    className="shrink-0 text-[var(--color-cream-faint)] transition-transform group-hover/attn:translate-x-0.5"
                    aria-hidden
                  >
                    ›
                  </span>
                </button>
              ))}
            </div>
          )}
        </Block>

        {/* quick access */}
        <Block delay={0.12}>
          <h4
            className="mb-2 text-[13px]"
            style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
          >
            ทางลัด
          </h4>
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            {QUICK.map((id) => {
              const meta = SECTION_BY_ID[id]
              const hex = ACCENT_HEX[meta.accent]
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => go(id)}
                  className="group/quick flex flex-col gap-1.5 border p-3 text-left transition-all hover:-translate-y-px"
                  style={{
                    borderRadius: 14,
                    borderColor: 'var(--color-line-soft)',
                    background: 'var(--color-surface-2)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = withAlpha(hex, 0.4)
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = 'var(--color-line-soft)'
                  }}
                >
                  <span
                    className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-lg"
                    style={{
                      background: `linear-gradient(150deg, ${withAlpha(hex, 0.24)}, ${withAlpha(hex, 0.08)})`,
                      border: `1px solid ${withAlpha(hex, 0.3)}`,
                    }}
                  >
                    {meta.glyph}
                  </span>
                  <span className="text-[12.5px] font-medium text-[var(--color-cream)]">{meta.label}</span>
                  <span className="line-clamp-2 text-[10.5px] leading-snug text-[var(--color-cream-faint)]">
                    {meta.desc}
                  </span>
                </button>
              )
            })}
          </div>
        </Block>
      </div>
    </Section>
  )
}

function Block({ children, delay }: { children: ReactNode; delay: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  )
}
