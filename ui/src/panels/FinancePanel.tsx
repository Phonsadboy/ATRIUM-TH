import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { useSelector, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Modal, Pill, withAlpha } from '../components/primitives'
import { ACCENT_HEX } from '../lib/visuals'
import { money, clamp } from '../lib/format'
import { isExec } from '../lib/threads'
import type { AccentName, CostCategory } from '../contract/types'

/* Thai labels + accent colour per cost category (v0.4 §28). */
const CATEGORY_LABEL: Record<CostCategory, string> = {
  work: 'งานจริง',
  chat: 'คุยเล่น',
  meeting: 'ประชุม/ส่งต่อ',
  autonomous: 'ริเริ่มเอง',
  memory: 'ความจำ',
  tool: 'เครื่องมือ',
}

const CATEGORY_HEX: Record<CostCategory, string> = {
  work: ACCENT_HEX.teal,
  chat: ACCENT_HEX.sky,
  meeting: ACCENT_HEX.lavender,
  autonomous: ACCENT_HEX.amber,
  memory: ACCENT_HEX.honey,
  tool: ACCENT_HEX.coral,
}

const CATEGORY_ORDER: CostCategory[] = [
  'work',
  'meeting',
  'tool',
  'autonomous',
  'chat',
  'memory',
]

/** Light department lookup — emoji + accent + name, exec filtered out for spend. */
interface DeptLite {
  id: string
  name: string
  emoji: string
  accent: AccentName
}

/** "จ.", "อ." … short Thai weekday + day-of-month from a YYYY-MM-DD key. */
const THAI_DOW = ['อา.', 'จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.']
function dayParts(key: string): { dow: string; dom: number } {
  const d = new Date(key + 'T00:00:00')
  return { dow: THAI_DOW[d.getDay()] ?? '', dom: d.getDate() }
}

export function FinancePanel() {
  const open = useUI((s) => s.financeOpen)
  const closeFinance = useUI((s) => s.closeFinance)

  // re-fetch the report whenever spend moves or a department comes/goes
  const spentToday = useSelector((s) => s.budget.spentTodayUsd)
  const dailyCap = useSelector((s) => s.budget.dailyCapUsd)
  const now = useSelector((s) => s.now)
  const departments = useSelector(
    (s) =>
      s.departments
        .filter((d) => !isExec(d.id))
        .map<DeptLite>((d) => ({ id: d.id, name: d.name, emoji: d.emoji, accent: d.accent })),
    (a, b) =>
      a.length === b.length &&
      a.every((x, i) => {
        const y = b[i]
        return x.id === y.id && x.name === y.name && x.emoji === y.emoji && x.accent === y.accent
      }),
  )

  // getCostReport is a plain getter; recompute when the modal opens or spend ticks.
  const report = useMemo(
    () => client.getCostReport('day'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, spentToday, now, departments.length],
  )

  const deptOf = useMemo(() => {
    const m = new Map<string, DeptLite>()
    for (const d of departments) m.set(d.id, d)
    return m
  }, [departments])

  return (
    <Modal
      open={open}
      onClose={closeFinance}
      width={960}
      title={
        <span className="flex items-baseline gap-3">
          การเงิน · สถิติการใช้จ่าย
          <span className="text-[11px] font-normal text-[var(--color-cream-faint)]">
            ย้อนหลัง 7 วัน
          </span>
        </span>
      }
    >
      <div className="space-y-4">
        {report.anomalies.length > 0 && <Anomalies items={report.anomalies} />}

        <Headline
          spentToday={report.spentUsd}
          forecast={report.forecastUsd}
          cap={dailyCap}
          capSpent={spentToday}
        />

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card title="แนวโน้มรายวัน" hint="ผลรวมต่อวัน 7 วันล่าสุด">
            <TrendChart series={report.series} forecast={report.forecastUsd} />
          </Card>

          <Card title="สัดส่วนตามหมวด" hint="รวมทั้งบริษัท · 7 วัน">
            <CategoryBreakdown byCategory={report.byCategory} />
          </Card>
        </div>

        <Card title="ใช้จ่ายรายฝ่าย" hint="จัดอันดับ · 7 วัน">
          <AgentBreakdown byAgent={report.byAgent} deptOf={deptOf} />
        </Card>
      </div>
    </Modal>
  )
}

/* ============================================================
   Shared shell
   ============================================================ */

function Card({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section
      className="rounded-2xl border p-4"
      style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
    >
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--color-cream)]">{title}</h3>
        {hint && <span className="text-[10px] text-[var(--color-cream-faint)]">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

function Mono({
  children,
  color = 'var(--color-cream)',
  className,
}: {
  children: React.ReactNode
  color?: string
  className?: string
}) {
  return (
    <span
      className={'tabular-nums ' + (className ?? '')}
      style={{ fontFamily: 'var(--font-mono)', color }}
    >
      {children}
    </span>
  )
}

/* ============================================================
   Anomalies
   ============================================================ */

function Anomalies({ items }: { items: string[] }) {
  const hex = ACCENT_HEX.coral
  return (
    <div className="space-y-1.5">
      {items.map((text, i) => (
        <div
          key={i}
          className="flex items-start gap-2 rounded-xl border px-3 py-2"
          style={{ borderColor: withAlpha(hex, 0.35), background: withAlpha(hex, 0.1) }}
        >
          <span className="mt-px text-[13px] leading-none" style={{ color: hex }}>
            ⚠
          </span>
          <span className="text-[12px] leading-relaxed text-[var(--color-cream)]">{text}</span>
        </div>
      ))}
    </div>
  )
}

/* ============================================================
   Headline — today, forecast (EOD + month), cap backstop
   ============================================================ */

function Headline({
  spentToday,
  forecast,
  cap,
  capSpent,
}: {
  spentToday: number
  forecast: number
  cap: number
  capSpent: number
}) {
  const monthForecast = forecast * 30
  const capPct = cap > 0 ? clamp(capSpent / cap, 0, 1) : 0
  const overCap = capSpent >= cap
  const capHex = overCap ? ACCENT_HEX.coral : capPct > 0.75 ? ACCENT_HEX.amber : ACCENT_HEX.teal

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {/* today + cap meter (spans 1 col, the hero) */}
      <div
        className="rounded-2xl border p-4 sm:col-span-1"
        style={{
          borderColor: withAlpha(ACCENT_HEX.amber, 0.3),
          background: `linear-gradient(150deg, ${withAlpha(ACCENT_HEX.amber, 0.12)}, ${withAlpha(
            ACCENT_HEX.amber,
            0.03,
          )})`,
        }}
      >
        <div className="text-[10px] font-medium tracking-wide text-[var(--color-cream-dim)]">
          ใช้จ่ายวันนี้
        </div>
        <Mono color={ACCENT_HEX.amber} className="text-[30px] leading-tight font-semibold">
          {money(spentToday)}
        </Mono>
        <div className="mt-2.5">
          <div
            className="h-1.5 w-full overflow-hidden rounded-full"
            style={{ background: 'var(--color-line-soft)' }}
          >
            <motion.div
              className="h-full rounded-full"
              initial={false}
              animate={{ width: `${Math.round(capPct * 100)}%` }}
              transition={{ type: 'spring', stiffness: 120, damping: 20 }}
              style={{ background: capHex, boxShadow: `0 0 10px ${withAlpha(capHex, 0.6)}` }}
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between text-[10px] text-[var(--color-cream-faint)]">
            <span>เพดานงบรวม/วัน</span>
            <CapEditor cap={cap} hex={capHex} />
          </div>
        </div>
      </div>

      <Stat
        label="คาดการณ์สิ้นวัน"
        value={money(forecast)}
        sub="ประมาณจากอัตราวันนี้"
        accent={ACCENT_HEX.teal}
      />
      <Stat
        label="คาดการณ์ต่อเดือน"
        value={money(monthForecast)}
        sub="≈ สิ้นวัน × 30"
        accent={ACCENT_HEX.lavender}
      />
    </div>
  )
}

/** Inline editor for the company-wide daily spend cap (PATCH /api/budget/company). */
function CapEditor({ cap, hex }: { cap: number; hex: string }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          setDraft(String(cap))
          setEditing(true)
        }}
        className="rounded px-1 transition-colors hover:bg-[var(--color-surface-3)]"
        title="แก้ไขเพดานงบรวมของบริษัทต่อวัน"
      >
        <Mono color={hex}>{money(cap)}</Mono>
        <span className="ml-1 text-[var(--color-cream-faint)]">แก้</span>
      </button>
    )
  }

  const commit = () => {
    const next = Number(draft)
    if (Number.isFinite(next) && next >= 0 && next !== cap) client.setCompanyBudget(next)
    setEditing(false)
  }

  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-[var(--color-cream-faint)]">$</span>
      <input
        autoFocus
        type="number"
        min={0}
        step={1}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') setEditing(false)
        }}
        className="w-16 rounded border bg-[var(--color-surface)] px-1.5 py-0.5 text-right text-[11px] tabular-nums text-[var(--color-cream)] outline-none focus:border-[var(--color-amber)]"
        style={{ borderColor: 'var(--color-line-soft)', fontFamily: 'var(--font-mono)' }}
      />
    </span>
  )
}

function Stat({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: string
  sub: string
  accent: string
}) {
  return (
    <div
      className="flex flex-col justify-between rounded-2xl border p-4"
      style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
    >
      <div className="text-[10px] font-medium tracking-wide text-[var(--color-cream-dim)]">
        {label}
      </div>
      <Mono color={accent} className="mt-1 text-[26px] leading-tight font-semibold">
        {value}
      </Mono>
      <div className="mt-1.5 text-[10px] text-[var(--color-cream-faint)]">{sub}</div>
    </div>
  )
}

/* ============================================================
   Daily-spend trend — SVG bars with a forecast ghost on today
   ============================================================ */

function TrendChart({
  series,
  forecast,
}: {
  series: { day: string; usd: number }[]
  forecast: number
}) {
  const W = 440
  const H = 150
  const padX = 8
  const padTop = 14
  const padBottom = 22
  const plotH = H - padTop - padBottom
  const plotW = W - padX * 2

  // the last bar is "today" (partial); show its forecast as a faint ghost cap
  const lastIdx = series.length - 1
  const peak = Math.max(
    0.01,
    ...series.map((s) => s.usd),
    forecast, // make sure the forecast ghost fits
  )
  const n = series.length || 1
  const slot = plotW / n
  const barW = Math.min(34, slot * 0.56)
  const yFor = (usd: number) => padTop + plotH * (1 - usd / peak)

  // a couple of horizontal gridlines for readability
  const gridVals = [peak, peak / 2]

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ display: 'block' }}>
        {gridVals.map((v, i) => {
          const y = yFor(v)
          return (
            <g key={i}>
              <line x1={padX} y1={y} x2={W - padX} y2={y} stroke="var(--color-line-soft)" strokeWidth={1} />
              <text x={W - padX} y={y - 3} textAnchor="end" fontSize={8} fill="var(--color-cream-faint)">
                {money(v)}
              </text>
            </g>
          )
        })}

        {series.map((s, i) => {
          const cx = padX + slot * (i + 0.5)
          const x = cx - barW / 2
          const isToday = i === lastIdx
          const top = yFor(s.usd)
          const baseY = padTop + plotH
          const hex = isToday ? ACCENT_HEX.amber : ACCENT_HEX.teal
          const { dow, dom } = dayParts(s.day)
          return (
            <g key={s.day}>
              {/* forecast ghost (only on today) */}
              {isToday && forecast > s.usd && (
                <motion.rect
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  x={x}
                  y={yFor(forecast)}
                  width={barW}
                  height={Math.max(0, baseY - yFor(forecast))}
                  rx={4}
                  fill={withAlpha(hex, 0.18)}
                  stroke={withAlpha(hex, 0.5)}
                  strokeDasharray="3 3"
                  strokeWidth={1}
                />
              )}
              <motion.rect
                initial={{ height: 0, y: baseY }}
                animate={{ height: Math.max(2, baseY - top), y: top }}
                transition={{ type: 'spring', stiffness: 160, damping: 22, delay: i * 0.03 }}
                x={x}
                width={barW}
                rx={4}
                fill={hex}
                style={{ filter: `drop-shadow(0 0 6px ${withAlpha(hex, 0.45)})` }}
              />
              <text
                x={cx}
                y={H - padBottom + 12}
                textAnchor="middle"
                fontSize={8.5}
                fill={isToday ? ACCENT_HEX.amber : 'var(--color-cream-faint)'}
                style={{ fontWeight: isToday ? 600 : 400 }}
              >
                {dow}
                {dom}
              </text>
            </g>
          )
        })}
      </svg>
      <div className="mt-1 flex items-center gap-3 px-1 text-[10px] text-[var(--color-cream-dim)]">
        <LegendSwatch hex={ACCENT_HEX.teal} label="วันก่อนหน้า" />
        <LegendSwatch hex={ACCENT_HEX.amber} label="วันนี้" />
        <span className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: withAlpha(ACCENT_HEX.amber, 0.18), border: `1px dashed ${withAlpha(ACCENT_HEX.amber, 0.6)}` }}
          />
          คาดการณ์
        </span>
      </div>
    </div>
  )
}

function LegendSwatch({ hex, label }: { hex: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: hex }} />
      {label}
    </span>
  )
}

/* ============================================================
   Category breakdown — stacked bar + legend
   ============================================================ */

function CategoryBreakdown({ byCategory }: { byCategory: Record<CostCategory, number> }) {
  const rows = CATEGORY_ORDER.map((c) => ({ c, usd: byCategory[c] ?? 0 }))
  const total = rows.reduce((s, r) => s + r.usd, 0)

  if (total <= 0) {
    return (
      <div className="py-8 text-center text-[12px] text-[var(--color-cream-faint)]">
        ยังไม่มีข้อมูลการใช้จ่าย
      </div>
    )
  }

  return (
    <div>
      {/* stacked bar */}
      <div
        className="flex h-3.5 w-full overflow-hidden rounded-full"
        style={{ background: 'var(--color-line-soft)' }}
      >
        {rows.map((r) => {
          const pct = (r.usd / total) * 100
          if (pct <= 0) return null
          return (
            <motion.div
              key={r.c}
              initial={{ width: 0 }}
              animate={{ width: `${pct}%` }}
              transition={{ type: 'spring', stiffness: 140, damping: 24 }}
              style={{ background: CATEGORY_HEX[r.c] }}
              title={`${CATEGORY_LABEL[r.c]} · ${money(r.usd)}`}
            />
          )
        })}
      </div>

      {/* legend grid */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
        {rows
          .slice()
          .sort((a, b) => b.usd - a.usd)
          .map((r) => {
            const hex = CATEGORY_HEX[r.c]
            const pct = total > 0 ? Math.round((r.usd / total) * 100) : 0
            return (
              <div key={r.c} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: hex }} />
                <span className="text-[11px] text-[var(--color-cream-dim)]">
                  {CATEGORY_LABEL[r.c]}
                </span>
                <span className="ml-auto flex items-baseline gap-1.5">
                  <Mono color={hex} className="text-[11px] font-semibold">
                    {money(r.usd)}
                  </Mono>
                  <span className="text-[10px] text-[var(--color-cream-faint)]">{pct}%</span>
                </span>
              </div>
            )
          })}
      </div>
    </div>
  )
}

/* ============================================================
   Per-agent breakdown — ranked horizontal bars
   ============================================================ */

function AgentBreakdown({
  byAgent,
  deptOf,
}: {
  byAgent: Record<string, number>
  deptOf: Map<string, DeptLite>
}) {
  const rows = Object.entries(byAgent)
    .map(([id, usd]) => ({ id, usd, dept: deptOf.get(id) }))
    .filter((r) => r.usd > 0)
    .sort((a, b) => b.usd - a.usd)

  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-[12px] text-[var(--color-cream-faint)]">
        ยังไม่มีข้อมูลการใช้จ่ายรายฝ่าย
      </div>
    )
  }

  const peak = Math.max(...rows.map((r) => r.usd))
  const total = rows.reduce((s, r) => s + r.usd, 0)

  return (
    <div className="space-y-2.5">
      {rows.map((r, i) => {
        const accent = r.dept ? ACCENT_HEX[r.dept.accent] : ACCENT_HEX.honey
        const name = r.dept?.name ?? r.id
        const emoji = r.dept?.emoji ?? '🏛️'
        const pct = total > 0 ? Math.round((r.usd / total) * 100) : 0
        const barPct = peak > 0 ? (r.usd / peak) * 100 : 0
        return (
          <div key={r.id} className="flex items-center gap-3">
            <span className="w-4 shrink-0 text-right text-[11px] text-[var(--color-cream-faint)]">
              {i + 1}
            </span>
            <span
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[14px]"
              style={{ background: withAlpha(accent, 0.16), border: `1px solid ${withAlpha(accent, 0.32)}` }}
            >
              {emoji}
            </span>
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="truncate text-[12px] font-medium text-[var(--color-cream)]">
                  ฝ่าย{name}
                </span>
                <span className="flex shrink-0 items-baseline gap-1.5">
                  <Mono color={accent} className="text-[12px] font-semibold">
                    {money(r.usd)}
                  </Mono>
                  <span className="text-[10px] text-[var(--color-cream-faint)]">{pct}%</span>
                </span>
              </div>
              <div
                className="h-2 w-full overflow-hidden rounded-full"
                style={{ background: 'var(--color-line-soft)' }}
              >
                <motion.div
                  className="h-full rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${barPct}%` }}
                  transition={{ type: 'spring', stiffness: 150, damping: 22, delay: i * 0.04 }}
                  style={{ background: accent, boxShadow: `0 0 8px ${withAlpha(accent, 0.5)}` }}
                />
              </div>
            </div>
          </div>
        )
      })}

      <div className="flex items-center justify-between border-t pt-2.5" style={{ borderColor: 'var(--color-line-soft)' }}>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-cream-dim)]">
          <Pill color={ACCENT_HEX.teal}>{rows.length} ฝ่าย</Pill>
          รวมใช้จ่าย 7 วัน
        </span>
        <Mono className="text-[13px] font-semibold">{money(total)}</Mono>
      </div>
    </div>
  )
}
