import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useUI } from '../../state/ui'
import type { ConsoleSection } from '../../state/ui'
import { withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { OVERVIEW, SECTION_GROUPS, SECTION_BY_ID } from './sections'
import type { SectionMeta } from './sections'
import { useAttention } from './useAttention'
import type { Attention } from './useAttention'
import { OverviewPanel } from './OverviewPanel'
import { ProjectsPanel } from './ProjectsPanel'
import { OnboardingPanel } from './OnboardingPanel'
import { ArtifactsPanel } from './ArtifactsPanel'
import { ToolsPanel } from './ToolsPanel'
import { MeetingsPanel } from './MeetingsPanel'
import { WarRoomsPanel } from './WarRoomsPanel'
import { PlaybooksPanel } from './PlaybooksPanel'
import { LessonsPanel } from './LessonsPanel'
import { TriggersPanel } from './TriggersPanel'
import { BulletinsPanel } from './BulletinsPanel'
import { AuditPanel } from './AuditPanel'
import { ConnectorsPanel } from './ConnectorsPanel'
import { SkillsPanel } from './SkillsPanel'
import { KnowledgeDebtPanel } from './KnowledgeDebtPanel'
import { OwnerPanel } from './OwnerPanel'
import { ExecutivePanel } from './ExecutivePanel'
import { NotificationPrefsPanel } from './NotificationPrefsPanel'
import { MemoryPanel } from './MemoryPanel'
import { ConfigDiffPanel } from './ConfigDiffPanel'

function renderSection(section: ConsoleSection, attention: Attention) {
  switch (section) {
    case 'overview':
      return <OverviewPanel attention={attention} />
    case 'projects':
      return <ProjectsPanel />
    case 'onboarding':
      return <OnboardingPanel />
    case 'artifacts':
      return <ArtifactsPanel />
    case 'tools':
      return <ToolsPanel />
    case 'meetings':
      return <MeetingsPanel />
    case 'warrooms':
      return <WarRoomsPanel />
    case 'playbooks':
      return <PlaybooksPanel />
    case 'lessons':
      return <LessonsPanel />
    case 'triggers':
      return <TriggersPanel />
    case 'bulletins':
      return <BulletinsPanel />
    case 'audit':
      return <AuditPanel />
    case 'connectors':
      return <ConnectorsPanel />
    case 'skills':
      return <SkillsPanel />
    case 'debt':
      return <KnowledgeDebtPanel />
    case 'memory':
      return <MemoryPanel />
    case 'config':
      return <ConfigDiffPanel />
    case 'owner':
      return <OwnerPanel />
    case 'executive':
      return <ExecutivePanel />
    case 'notifyPrefs':
      return <NotificationPrefsPanel />
  }
}

/** A small accent-tinted square holding the section glyph. */
function Glyph({ meta, size = 30 }: { meta: SectionMeta; size?: number }) {
  const hex = ACCENT_HEX[meta.accent]
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.5,
        borderRadius: size * 0.3,
        background: `linear-gradient(150deg, ${withAlpha(hex, 0.26)}, ${withAlpha(hex, 0.08)})`,
        border: `1px solid ${withAlpha(hex, 0.32)}`,
      }}
    >
      {meta.glyph}
    </span>
  )
}

function NavItem({
  meta,
  active,
  count,
  onClick,
}: {
  meta: SectionMeta
  active: boolean
  count: number
  onClick: () => void
}) {
  const hex = ACCENT_HEX[meta.accent]
  return (
    <button
      type="button"
      onClick={onClick}
      title={meta.desc}
      className="group/nav relative flex w-full items-center gap-2.5 rounded-xl px-2 py-1.5 text-left transition-colors"
      style={{
        background: active ? withAlpha(hex, 0.13) : 'transparent',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = 'var(--color-surface-2)'
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = 'transparent'
      }}
    >
      {active && (
        <span
          aria-hidden
          className="absolute top-1/2 left-0 h-5 w-[3px] -translate-y-1/2 rounded-r-full"
          style={{ background: hex, boxShadow: `0 0 10px ${withAlpha(hex, 0.7)}` }}
        />
      )}
      <Glyph meta={meta} size={26} />
      <span
        className="min-w-0 flex-1 truncate text-[13px] font-medium"
        style={{ color: active ? 'var(--color-cream)' : 'var(--color-cream-dim)' }}
      >
        {meta.label}
      </span>
      {count > 0 && (
        <span
          className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-bold tabular-nums"
          style={{ background: withAlpha(hex, 0.18), color: hex, border: `1px solid ${withAlpha(hex, 0.4)}` }}
        >
          {count}
        </span>
      )}
    </button>
  )
}

export function Console() {
  const open = useUI((s) => s.consoleOpen)
  const section = useUI((s) => s.consoleSection)
  const setSection = useUI((s) => s.setConsoleSection)
  const close = useUI((s) => s.closeConsole)
  const [query, setQuery] = useState('')

  const attention = useAttention(open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, close])

  // clear the search box whenever the console is dismissed
  useEffect(() => {
    if (open) return
    let cancelled = false
    queueMicrotask(() => {
      if (!cancelled) setQuery('')
    })
    return () => {
      cancelled = true
    }
  }, [open])

  const active = SECTION_BY_ID[section] ?? OVERVIEW
  const badgeFor = (meta: SectionMeta) => (meta.badge ? attention.badges[meta.badge] : 0)

  const q = query.trim().toLowerCase()
  const matches = useMemo(() => {
    if (!q) return null
    const all = [OVERVIEW, ...SECTION_GROUPS.flatMap((g) => g.items)]
    return all.filter((m) => m.label.toLowerCase().includes(q) || m.desc.toLowerCase().includes(q))
  }, [q])

  const go = (id: ConsoleSection) => {
    setSection(id)
    setQuery('')
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div
            className="absolute inset-0 backdrop-blur-sm"
            style={{ background: 'rgba(8,6,3,0.62)' }}
            onClick={close}
          />
          <motion.div
            className="panel absolute inset-2.5 z-10 flex overflow-hidden sm:inset-4"
            style={{ borderRadius: 18 }}
            role="dialog"
            aria-modal="true"
            aria-label="ศูนย์ปฏิบัติการ"
            initial={{ scale: 0.985, y: 10, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.99, y: 6, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          >
            {/* ───────── nav rail ───────── */}
            <nav
              className="flex w-[244px] shrink-0 flex-col"
              style={{ borderRight: '1px solid var(--color-line-soft)', background: 'var(--color-surface)' }}
            >
              <div className="px-3.5 pt-4 pb-3">
                <div className="flex items-center gap-2">
                  <span className="text-lg">🛰️</span>
                  <div
                    className="text-[15px] leading-none"
                    style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
                  >
                    ศูนย์ปฏิบัติการ
                  </div>
                </div>
                <div className="mt-1 pl-7 text-[10.5px] text-[var(--color-cream-faint)]">
                  บริหารงานทั้งบริษัทจากที่เดียว
                </div>
              </div>

              {/* search */}
              <div className="px-3 pb-2">
                <div
                  className="flex items-center gap-2 rounded-xl border px-2.5 py-1.5"
                  style={{ borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
                >
                  <span className="text-[12px] opacity-60">🔍</span>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="ค้นหาเมนู…"
                    className="w-full bg-transparent text-[12px] text-[var(--color-cream)] outline-none placeholder:text-[var(--color-cream-faint)]"
                  />
                  {query && (
                    <button
                      type="button"
                      onClick={() => setQuery('')}
                      className="text-[11px] text-[var(--color-cream-faint)] hover:text-[var(--color-cream)]"
                      aria-label="ล้างคำค้น"
                    >
                      ✕
                    </button>
                  )}
                </div>
              </div>

              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pt-1 pb-3">
                {matches ? (
                  <div>
                    <div className="mb-1 px-1 text-[9px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
                      ผลการค้นหา · {matches.length}
                    </div>
                    {matches.length === 0 ? (
                      <div className="px-1 py-2 text-[11px] text-[var(--color-cream-faint)]">
                        ไม่พบเมนูที่ตรงกับ “{query}”
                      </div>
                    ) : (
                      <div className="space-y-0.5">
                        {matches.map((m) => (
                          <NavItem
                            key={m.id}
                            meta={m}
                            active={section === m.id}
                            count={badgeFor(m)}
                            onClick={() => go(m.id)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    <div className="space-y-0.5">
                      <NavItem
                        meta={OVERVIEW}
                        active={section === OVERVIEW.id}
                        count={0}
                        onClick={() => go(OVERVIEW.id)}
                      />
                    </div>
                    {SECTION_GROUPS.map((group) => (
                      <div key={group.id}>
                        <div className="mb-1 px-1 text-[9px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
                          {group.label}
                        </div>
                        <div className="space-y-0.5">
                          {group.items.map((item) => (
                            <NavItem
                              key={item.id}
                              meta={item}
                              active={section === item.id}
                              count={badgeFor(item)}
                              onClick={() => go(item.id)}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </nav>

            {/* ───────── content ───────── */}
            <div className="flex min-h-0 flex-1 flex-col" style={{ background: 'var(--color-ink)' }}>
              {/* header */}
              <div
                className="flex items-center gap-3 px-5 py-3.5"
                style={{ borderBottom: '1px solid var(--color-line-soft)' }}
              >
                <Glyph meta={active} size={38} />
                <div className="min-w-0 flex-1">
                  <h2
                    className="truncate text-[18px] leading-tight"
                    style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
                  >
                    {active.label}
                  </h2>
                  <div className="truncate text-[11.5px] text-[var(--color-cream-dim)]">{active.desc}</div>
                </div>
                <button
                  type="button"
                  onClick={close}
                  aria-label="ปิดศูนย์ปฏิบัติการ"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border text-[var(--color-cream-faint)] transition-colors hover:text-[var(--color-cream)]"
                  style={{ borderColor: 'var(--color-line-soft)' }}
                >
                  ✕
                </button>
              </div>

              {/* body */}
              <div className="min-h-0 flex-1 overflow-hidden px-5 py-4">
                <motion.div
                  key={section}
                  className="flex h-full min-h-0 flex-col"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                >
                  {renderSection(section, attention)}
                </motion.div>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
