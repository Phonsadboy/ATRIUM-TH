/* eslint-disable react-refresh/only-export-components */
// Shared building blocks for the operations Console: a small async-fetch hook
// plus the loading / empty / error / section-shell primitives every section
// panel reuses. Helpers + components live together intentionally (imported
// across all console section files).
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ProblemDetails } from '../../components/ProblemDetails'
import { withAlpha } from '../../components/primitives'
import { ACCENT_HEX } from '../../lib/visuals'
import { useSelector, shallowArrayEqual } from '../../state/useCompany'
import { EXEC_ID } from '../../lib/threads'
import type { AccentName, Department } from '../../contract/types'

export interface DeptLite {
  id: string
  name: string
  emoji: string
  accent: AccentName
  isExec: boolean
}

/** Department directory for the console — id → emoji/name/accent, plus a
 *  formatter that resolves the executive and special actors to friendly names. */
export function useDepts(): { list: DeptLite[]; label: (id: string | null | undefined) => string } {
  const list = useSelector(
    (s) =>
      s.departments.map<DeptLite>((d: Department) => ({
        id: d.id,
        name: d.name,
        emoji: d.emoji,
        accent: d.accent,
        isExec: d.id === EXEC_ID,
      })),
    (a, b) => shallowArrayEqual(a.map((x) => x.id + x.name + x.emoji), b.map((x) => x.id + x.name + x.emoji)),
  )
  const label = (id: string | null | undefined): string => {
    if (!id) return '—'
    if (id === 'system') return 'ระบบ'
    if (id === 'user' || id === 'owner') return 'คุณ'
    if (id === EXEC_ID || id === 'executive' || id === 'exec') return 'ผู้บริหาร'
    const d = list.find((x) => x.id === id)
    return d ? `${d.emoji} ${d.name}` : id
  }
  return { list, label }
}

/** The company server clock (ms). Use this instead of Date.now() in render so
 *  relative-time labels stay pure and refresh on each backend tick. */
export function useNow(): number {
  return useSelector((s) => s.now)
}

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

/**
 * Run `fn` on mount and whenever `deps` change, tracking loading/error.
 * `reload()` re-runs imperatively (e.g. after a mutation). The latest call
 * wins — stale resolutions are dropped so fast section switches never flicker
 * old data in.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: string | null }>({
    data: null,
    loading: true,
    error: null,
  })
  const [nonce, setNonce] = useState(0)
  const runId = useRef(0)

  useEffect(() => {
    const id = ++runId.current
    // Canonical data-fetch effect: flip to loading on (re)run, then resolve
    // async. The synchronous setState here is intentional and bounded.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((s) => ({ ...s, loading: true, error: null }))
    fn()
      .then((data) => {
        if (id === runId.current) setState({ data, loading: false, error: null })
      })
      .catch((e: unknown) => {
        if (id === runId.current) {
          setState({ data: null, loading: false, error: e instanceof Error ? e.message : String(e) })
        }
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, reload: () => setNonce((n) => n + 1) }
}

/** Section shell: an optional right-aligned action toolbar, then a scrollable
 *  body. The section title + description are rendered once by the Console shell
 *  header (from sections.ts), so panels no longer draw their own heading — they
 *  just hand their `actions` and `children` here. `title`/`hint` are still
 *  accepted for back-compat but intentionally not drawn. */
export function Section({
  actions,
  children,
}: {
  title?: string
  hint?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {actions && (
        <div className="mb-3 flex flex-wrap items-center justify-end gap-2">{actions}</div>
      )}
      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto pr-1">{children}</div>
    </div>
  )
}

export function Loading() {
  return (
    <div className="mt-20 flex flex-col items-center gap-3 text-center">
      <span
        className="inline-block h-5 w-5 animate-spin rounded-full border-2"
        style={{ borderColor: withAlpha(ACCENT_HEX.amber, 0.25), borderTopColor: ACCENT_HEX.amber }}
      />
      <span className="text-[12px] text-[var(--color-cream-faint)]">กำลังโหลด…</span>
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return (
    <div
      className="mt-8 flex flex-col items-center gap-2 rounded-[14px] border border-dashed px-6 py-12 text-center"
      style={{ borderColor: 'var(--color-line-soft)' }}
    >
      <span className="text-2xl opacity-60">🗂️</span>
      <span className="max-w-sm text-[12px] leading-relaxed text-[var(--color-cream-faint)]">{text}</span>
    </div>
  )
}

export function ErrorNote({ error, onRetry }: { error: string; onRetry?: () => void }) {
  const hex = ACCENT_HEX.coral
  return (
    <div
      className="mt-3 rounded-[14px] border px-3.5 py-3"
      style={{ borderColor: withAlpha(hex, 0.35), background: withAlpha(hex, 0.08) }}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 text-[12px] leading-relaxed text-[var(--color-cream)] break-words [overflow-wrap:anywhere]">
          <span className="font-semibold" style={{ color: hex }}>ยังเรียกข้อมูลส่วนนี้ไม่ได้</span>
          <span className="ml-1 text-[var(--color-cream-faint)]">เปิดรายละเอียดเพื่อดูสาเหตุจาก backend</span>
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded-lg border px-2.5 py-1 text-[11px] text-[var(--color-cream-dim)] transition-colors hover:text-[var(--color-cream)]"
            style={{ borderColor: withAlpha(hex, 0.35) }}
          >
            ลองใหม่
          </button>
        )}
      </div>
      <ProblemDetails
        className="mt-2"
        color={hex}
        summary="ดูรายละเอียดการเรียกข้อมูล"
        rows={[
          { label: 'surface', value: 'console panel' },
          { label: 'error', value: error },
        ]}
      />
    </div>
  )
}

/** A bordered card used for list rows across sections. When an `accent` is
 *  given it gets a colored left spine so status reads at a glance. */
export function Row({
  children,
  onClick,
  accent,
}: {
  children: ReactNode
  onClick?: () => void
  accent?: string
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      {...(onClick ? { type: 'button' as const, onClick } : {})}
      className={
        'group/row relative block w-full min-w-0 overflow-hidden border p-3.5 text-left transition-all duration-150 ' +
        (onClick ? 'hover:-translate-y-px' : '')
      }
      style={{
        borderRadius: 12,
        borderColor: accent ? withAlpha(accent, 0.26) : 'var(--color-line-soft)',
        background:
          'linear-gradient(160deg, color-mix(in oklab, var(--color-surface-2), white 2.5%), var(--color-surface-2))',
        boxShadow: onClick ? '0 1px 0 rgba(0,0,0,0.25)' : undefined,
      }}
    >
      {accent && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-0 w-[3px] rounded-r"
          style={{ background: accent, opacity: 0.6 }}
        />
      )}
      {children}
    </Tag>
  )
}

/** Primary action button (amber). */
export function PrimaryBtn({
  children,
  onClick,
  disabled,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="rounded-[10px] px-3.5 py-2 text-xs font-semibold transition-all hover:brightness-105 disabled:opacity-40 disabled:hover:brightness-100"
      style={{
        background: `linear-gradient(180deg, ${ACCENT_HEX.amber}, ${ACCENT_HEX.amber})`,
        color: '#1a1610',
        boxShadow: disabled ? 'none' : `0 0 18px -6px ${withAlpha(ACCENT_HEX.amber, 0.7)}`,
      }}
    >
      {children}
    </button>
  )
}

/** Secondary / ghost button. */
export function GhostBtn({
  children,
  onClick,
  tint,
}: {
  children: ReactNode
  onClick?: () => void
  tint?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[10px] border px-3 py-2 text-xs font-medium transition-colors hover:text-[var(--color-cream)]"
      style={{
        borderColor: tint ? withAlpha(tint, 0.4) : 'var(--color-line-soft)',
        color: tint ?? 'var(--color-cream-dim)',
        background: tint ? withAlpha(tint, 0.07) : 'transparent',
      }}
    >
      {children}
    </button>
  )
}

/** Segmented control used by panels with internal views (tools, memory, …). */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[]
  value: T
  onChange: (id: T) => void
}) {
  return (
    <div
      className="mb-3 flex gap-1 rounded-xl p-1"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)' }}
    >
      {tabs.map((t) => {
        const on = value === t.id
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onChange(t.id)}
            className="flex-1 rounded-lg px-2 py-1.5 text-[11.5px] font-medium transition-all"
            style={
              on
                ? { background: ACCENT_HEX.amber, color: '#1a1610', boxShadow: `0 0 14px -5px ${withAlpha(ACCENT_HEX.amber, 0.8)}` }
                : { color: 'var(--color-cream-dim)' }
            }
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

/** Soft bordered container for forms / grouped controls inside a panel. */
export function FormCard({ children, title }: { children: ReactNode; title?: ReactNode }) {
  return (
    <div
      className="mb-3 border p-3.5"
      style={{ borderRadius: 14, borderColor: 'var(--color-line-soft)', background: 'var(--color-surface-2)' }}
    >
      {title && <SubLabel>{title}</SubLabel>}
      {children}
    </div>
  )
}

/** Small uppercase-ish caption that introduces a block inside a panel. */
export function SubLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mb-1.5 text-[11px] font-semibold tracking-wide text-[var(--color-cream-dim)]">{children}</div>
  )
}

/** Compact metric tile — label over a big value, optional accent + sublabel.
 *  Shared so the Overview and section panels show stats the same way. */
export function StatTile({
  label,
  value,
  sub,
  accent = ACCENT_HEX.amber,
  onClick,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  accent?: string
  onClick?: () => void
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      {...(onClick ? { type: 'button' as const, onClick } : {})}
      className={
        'relative flex min-w-0 flex-col gap-1 overflow-hidden border p-3.5 text-left transition-all ' +
        (onClick ? 'hover:-translate-y-px' : '')
      }
      style={{
        borderRadius: 14,
        borderColor: withAlpha(accent, 0.22),
        background:
          'linear-gradient(160deg, color-mix(in oklab, var(--color-surface-2), white 3%), var(--color-surface))',
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute -top-8 -right-6 h-20 w-20 rounded-full blur-2xl"
        style={{ background: withAlpha(accent, 0.16) }}
      />
      <span className="text-[10.5px] font-medium tracking-wide text-[var(--color-cream-faint)] uppercase">
        {label}
      </span>
      <span
        className="text-[22px] leading-none tabular-nums"
        style={{ fontFamily: 'var(--font-display)', color: 'var(--color-cream)' }}
      >
        {value}
      </span>
      {sub && <span className="text-[11px] text-[var(--color-cream-dim)]">{sub}</span>}
    </Tag>
  )
}
