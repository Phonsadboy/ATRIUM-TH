import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useSelector, shallowArrayEqual } from '../state/useCompany'
import { Dot, withAlpha } from '../components/primitives'
import { ACCENT_HEX, SEVERITY_HEX } from '../lib/visuals'
import { relTime } from '../lib/format'
import { isExec } from '../lib/threads'
import type { ActivityType } from '../contract/types'
import { Icon, type IconName } from '../components/Icon'

const TYPE_ICON: Record<ActivityType, IconName> = {
  task_created: 'tasks',
  task_assigned: 'send',
  task_progress: 'clock',
  task_done: 'approve',
  handoff: 'send',
  state_change: 'autonomy',
  compaction: 'memory',
  spend: 'budget',
  approval: 'approve',
  autonomous: 'autonomy',
  message: 'chat',
  system: 'model',
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
    >
      <path d="M6 15l6-6 6 6" />
    </svg>
  )
}

export function ActivityTicker() {
  const [open, setOpen] = useState(false)
  const events = useSelector((s) => s.activity.slice(0, 6), shallowArrayEqual)
  const now = useSelector((s) => s.now)
  const departments = useSelector(
    (s) =>
      s.departments.map((d) => ({
        id: d.id,
        emoji: d.emoji,
        name: d.name,
        agentName: d.agentName,
        accent: d.accent,
      })),
    (a, b) => a.length === b.length && a.every((x, i) => x.id === b[i].id),
  )
  const deptOf = (id: string | null) => (id ? departments.find((d) => d.id === id) : undefined)
  const latest = events[0]

  return (
    <div
      className="pointer-events-none absolute bottom-3 left-3 z-10"
      style={{ width: 'min(440px, calc(100% - 24px))' }}
    >
      <div
        className="panel pointer-events-auto overflow-hidden"
        style={{ background: withAlpha('#1a1610', 0.86), backdropFilter: 'blur(10px)' }}
      >
        {/* header doubles as the collapsed preview; click toggles */}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-[var(--color-surface-2)]"
        >
          <Dot color={SEVERITY_HEX.good} size={6} pulse />
          {open || !latest ? (
            <span className="flex-1 text-[10px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
              ความเคลื่อนไหวสด
            </span>
          ) : (
            <>
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md"
                style={{ background: withAlpha(SEVERITY_HEX[latest.severity], 0.16) }}
              >
                <Icon name={TYPE_ICON[latest.type]} size={12} />
              </span>
              <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--color-cream-dim)]">
                {latest.text}
              </span>
              <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-cream-faint)]">
                {relTime(latest.ts, now)}
              </span>
            </>
          )}
          <span className="shrink-0 text-[var(--color-cream-faint)]">
            <Chevron open={open} />
          </span>
        </button>

        {/* expanded feed */}
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="feed"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 320, damping: 32 }}
              className="overflow-hidden"
            >
              <div
                className="max-h-[300px] space-y-px overflow-y-auto px-1.5 pb-2"
                style={{ borderTop: '1px solid var(--color-line-soft)' }}
              >
                <AnimatePresence initial={false}>
                  {events.map((e) => {
                    const d = deptOf(e.departmentId)
                    const col = SEVERITY_HEX[e.severity]
                    return (
                      <motion.div
                        key={e.id}
                        layout
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                        className="flex items-start gap-2.5 rounded-lg px-1.5 py-1.5"
                      >
                        <span
                          className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-md"
                          style={{ background: withAlpha(col, 0.16) }}
                        >
                          <Icon name={TYPE_ICON[e.type]} size={12} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] leading-snug text-[var(--color-cream-dim)]">
                            {e.text}
                          </div>
                          {d && (
                            <div className="mt-0.5 flex items-center gap-1 text-[10px] leading-none">
                              <span className="text-[9px]">{d.emoji}</span>
                              <span style={{ color: ACCENT_HEX[d.accent] }}>{d.agentName}</span>
                              <span className="text-[var(--color-cream-faint)]">
                                · {isExec(d.id) ? d.name : `ฝ่าย${d.name}`}
                              </span>
                            </div>
                          )}
                        </div>
                        <span className="mt-px shrink-0 text-[10px] tabular-nums text-[var(--color-cream-faint)]">
                          {relTime(e.ts, now)}
                        </span>
                      </motion.div>
                    )
                  })}
                </AnimatePresence>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
