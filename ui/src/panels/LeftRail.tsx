import type { ReactNode } from 'react'
import { useSelector, useCompany, client } from '../state/useCompany'
import { useUI } from '../state/ui'
import { Avatar, Dot, Toggle, cx, withAlpha } from '../components/primitives'
import { PanelStrip } from '../components/PanelStrip'
import { STATE_HEX, STATE_LABEL, ACCENT_HEX } from '../lib/visuals'
import { useIsDesktop } from '../lib/useMedia'
import { EXEC_ID } from '../lib/threads'
import type { Department } from '../contract/types'

function DeptRow({ d, selected }: { d: Department; selected: boolean }) {
  const select = useUI((s) => s.select)
  const setRightTab = useUI((s) => s.setRightTab)
  return (
    <button
      type="button"
      onClick={() => {
        select(d.id)
        setRightTab('chat')
      }}
      className={cx(
        'group w-full rounded-2xl border p-2.5 text-left transition-all',
      )}
      style={{
        borderColor: selected ? withAlpha(ACCENT_HEX[d.accent], 0.5) : 'var(--color-line-soft)',
        background: selected ? withAlpha(ACCENT_HEX[d.accent], 0.1) : 'transparent',
      }}
    >
      <div className="flex items-center gap-2.5">
        <Avatar emoji={d.emoji} accent={d.accent} state={d.state} size={34} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-[var(--color-cream)]">
              {d.name}
            </span>
            {d.autonomy && (
              <span
                title="ทำงานเองได้"
                className="text-[9px]"
                style={{ color: ACCENT_HEX.teal }}
              >
                ⌁
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-[11px]">
            <Dot color={STATE_HEX[d.state]} size={6} pulse={d.state === 'working'} />
            <span className="truncate" style={{ color: STATE_HEX[d.state] }}>
              {STATE_LABEL[d.state]}
            </span>
            <span className="truncate text-[var(--color-cream-faint)]">· {d.agentName}</span>
          </div>
        </div>
      </div>
      <div
        className="mt-2 truncate text-[11px] text-[var(--color-cream-faint)]"
        title={d.role}
      >
        {d.role}
      </div>
    </button>
  )
}

export function LeftRail() {
  const departments = useSelector((s) => s.departments)
  const objectives = useCompany().objectives
  const selectedDeptId = useUI((s) => s.selectedDeptId)
  const openCreateDept = useUI((s) => s.openCreateDept)
  const leftCollapsed = useUI((s) => s.leftCollapsed)
  const toggleLeft = useUI((s) => s.toggleLeft)
  const isDesktop = useIsDesktop()

  const exec = departments.find((d) => d.id === EXEC_ID)
  const rest = departments.filter((d) => d.id !== EXEC_ID)

  if (isDesktop && leftCollapsed) {
    return (
      <aside className="flex min-h-0 flex-col" style={{ borderRight: '1px solid var(--color-line-soft)' }}>
        <PanelStrip label="แผนก" dir="open-right" onExpand={() => toggleLeft(false)} />
      </aside>
    )
  }

  return (
    <aside
      className="flex min-h-0 flex-col"
      style={{ borderRight: '1px solid var(--color-line-soft)' }}
    >
      {isDesktop && (
        <div className="flex items-center justify-between px-3 pt-3 pb-1.5">
          <span className="text-[10px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase">
            พื้นที่ทำงาน
          </span>
          <button
            type="button"
            onClick={() => toggleLeft(true)}
            title="พับแผงด้านซ้าย"
            aria-label="พับแผงด้านซ้าย"
            className="flex h-7 w-7 items-center justify-center rounded-lg border text-[14px] leading-none text-[var(--color-cream-dim)] transition-colors hover:border-[var(--color-line)] hover:text-[var(--color-cream)]"
            style={{ borderColor: 'var(--color-line-soft)' }}
          >
            ‹
          </button>
        </div>
      )}
      <div className="flex-1 space-y-5 overflow-y-auto px-3 pb-3 pt-1">
        {/* executive */}
        {exec && (
          <div>
            <RailLabel>ผู้บริหาร</RailLabel>
            <DeptRow d={exec} selected={selectedDeptId === exec.id} />
          </div>
        )}

        {/* departments */}
        <div>
          <div className="mb-2 flex items-center justify-between px-1">
            <RailLabel inline>แผนก ({rest.length})</RailLabel>
            <button
              type="button"
              onClick={openCreateDept}
              className="rounded-lg px-2 py-1 text-[11px] font-medium transition-colors"
              style={{ color: ACCENT_HEX.amber }}
            >
              + เปิดแผนกใหม่
            </button>
          </div>
          <div className="space-y-2">
            {rest.map((d) => (
              <DeptRow key={d.id} d={d} selected={selectedDeptId === d.id} />
            ))}
          </div>
        </div>

        {/* scheduler */}
        <div>
          <RailLabel>งานอัตโนมัติ</RailLabel>
          <div className="space-y-1.5">
            {objectives.map((o) => {
              const dept = departments.find((d) => d.id === o.departmentId)
              return (
                <div
                  key={o.id}
                  className="flex items-center gap-2 rounded-xl border p-2.5"
                  style={{ borderColor: 'var(--color-line-soft)' }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] text-[var(--color-cream)]">
                      {o.title}
                    </div>
                    <div className="truncate text-[11px] text-[var(--color-cream-faint)]">
                      {dept?.emoji} {dept?.name} · {o.cadence}
                    </div>
                  </div>
                  <Toggle
                    on={o.enabled}
                    onChange={(next) => client.toggleObjective(o.id, next)}
                  />
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </aside>
  )
}

function RailLabel({ children, inline }: { children: ReactNode; inline?: boolean }) {
  return (
    <div
      className={cx(
        'text-[10px] font-semibold tracking-[0.14em] text-[var(--color-cream-faint)] uppercase',
        !inline && 'mb-2 px-1',
      )}
    >
      {children}
    </div>
  )
}
