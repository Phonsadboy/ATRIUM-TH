/* eslint-disable react-refresh/only-export-components */
// This module intentionally mixes component + helper exports (cx, inputClass,
// withAlpha). The helpers are imported across many files, so they stay here;
// react-refresh's fast-refresh warning does not apply to a shared primitives module.
import { useEffect } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { clsx } from 'clsx'
import { motion, AnimatePresence } from 'framer-motion'
import type { AccentName, AgentState } from '../contract/types'
import { ACCENT_HEX, accentSoft, STATE_HEX } from '../lib/visuals'

export const cx = clsx

export function Panel({
  children,
  className,
  style,
}: {
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  return (
    <section className={cx('panel', className)} style={style}>
      {children}
    </section>
  )
}

export function Dot({
  color,
  pulse,
  size = 8,
}: {
  color: string
  pulse?: boolean
  size?: number
}) {
  return (
    <span
      className={cx('inline-block rounded-full', pulse && 'live-dot')}
      style={{
        width: size,
        height: size,
        background: color,
        boxShadow: `0 0 8px ${color}`,
      }}
    />
  )
}

export function Pill({
  children,
  color = '#aba090',
  soft = true,
  className,
}: {
  children: ReactNode
  color?: string
  soft?: boolean
  className?: string
}) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium whitespace-nowrap',
        className,
      )}
      style={{
        color,
        background: soft ? withAlpha(color, 0.12) : 'transparent',
        border: `1px solid ${withAlpha(color, 0.3)}`,
      }}
    >
      {children}
    </span>
  )
}

export function Progress({
  value,
  color = ACCENT_HEX.amber,
  className,
}: {
  value: number
  color?: string
  className?: string
}) {
  return (
    <div
      className={cx('h-1.5 w-full overflow-hidden rounded-full', className)}
      style={{ background: 'var(--color-line-soft)' }}
    >
      <motion.div
        className="h-full rounded-full"
        initial={false}
        animate={{ width: `${Math.round(value * 100)}%` }}
        transition={{ type: 'spring', stiffness: 120, damping: 20 }}
        style={{ background: color, boxShadow: `0 0 10px ${withAlpha(color, 0.6)}` }}
      />
    </div>
  )
}

export function Avatar({
  emoji,
  accent,
  size = 38,
  state,
}: {
  emoji: string
  accent: AccentName
  size?: number
  state?: AgentState
}) {
  const hex = ACCENT_HEX[accent]
  return (
    <span
      className="relative inline-flex shrink-0 items-center justify-center rounded-2xl"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.5,
        background: `linear-gradient(150deg, ${accentSoft(accent, 0.28)}, ${accentSoft(accent, 0.08)})`,
        border: `1px solid ${withAlpha(hex, 0.4)}`,
      }}
    >
      {emoji}
      {state && (
        <span
          className="absolute -right-1 -bottom-1 rounded-full"
          style={{
            width: size * 0.28,
            height: size * 0.28,
            background: STATE_HEX[state],
            border: '2px solid var(--color-surface)',
          }}
        />
      )}
    </span>
  )
}

export function Toggle({
  on,
  onChange,
  accent = ACCENT_HEX.teal,
}: {
  on: boolean
  onChange: (next: boolean) => void
  accent?: string
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className="relative h-5 w-9 shrink-0 rounded-full transition-colors"
      style={{
        background: on ? accent : 'var(--color-surface-4)',
      }}
      aria-pressed={on}
    >
      <motion.span
        className="absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white"
        animate={{ x: on ? 16 : 0 }}
        transition={{ type: 'spring', stiffness: 500, damping: 32 }}
      />
    </button>
  )
}

export function IconBtn({
  children,
  onClick,
  title,
  active,
  className,
}: {
  children: ReactNode
  onClick?: () => void
  title?: string
  active?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cx(
        'inline-flex h-9 w-9 items-center justify-center rounded-xl border transition-colors',
        active
          ? 'text-[var(--color-amber)]'
          : 'text-[var(--color-cream-dim)] hover:text-[var(--color-cream)]',
        className,
      )}
      style={{
        borderColor: active ? withAlpha(ACCENT_HEX.amber, 0.4) : 'var(--color-line-soft)',
        background: active ? accentSoft('amber', 0.12) : 'transparent',
      }}
    >
      {children}
    </button>
  )
}

export function Modal({
  open,
  onClose,
  children,
  title,
  width = 460,
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
  title?: ReactNode
  width?: number
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div
            className="absolute inset-0 backdrop-blur-sm"
            style={{ background: 'rgba(8,6,3,0.6)' }}
            onClick={onClose}
          />
          <motion.div
            className="panel relative z-10 max-h-[calc(100vh-32px)] w-full overflow-y-auto p-5"
            style={{ maxWidth: width }}
            role="dialog"
            aria-modal="true"
            initial={{ scale: 0.94, y: 16, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.96, y: 10, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 280, damping: 26 }}
          >
            <button
              type="button"
              onClick={onClose}
              aria-label="ปิด"
              className="absolute top-4 right-4 z-10 text-[var(--color-cream-faint)] hover:text-[var(--color-cream)]"
            >
              ✕
            </button>
            {title && (
              <h2 className="mb-4 pr-7 text-lg" style={{ fontFamily: 'var(--font-display)' }}>
                {title}
              </h2>
            )}
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function Drawer({
  open,
  onClose,
  children,
  title,
  width = 420,
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
  title?: ReactNode
  width?: number
}) {
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
            className="absolute inset-0"
            style={{ background: 'rgba(8,6,3,0.5)' }}
            onClick={onClose}
          />
          <motion.div
            className="panel absolute top-3 right-3 bottom-3 flex flex-col p-4"
            style={{ width, borderRadius: 18 }}
            initial={{ x: width + 30, opacity: 0.6 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: width + 30, opacity: 0.4 }}
            transition={{ type: 'spring', stiffness: 280, damping: 30 }}
          >
            {title && (
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-lg" style={{ fontFamily: 'var(--font-display)' }}>
                  {title}
                </h2>
                <button
                  type="button"
                  onClick={onClose}
                  className="text-[var(--color-cream-faint)] hover:text-[var(--color-cream)]"
                >
                  ✕
                </button>
              </div>
            )}
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <label className="block">
      <div className="mb-1 text-[11px] font-medium tracking-wide text-[var(--color-cream-dim)]">
        {label}
      </div>
      {children}
      {hint && (
        <div className="mt-1 text-[11px] text-[var(--color-cream-faint)]">{hint}</div>
      )}
    </label>
  )
}

export const inputClass =
  'w-full rounded-xl border bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-cream)] outline-none transition-colors placeholder:text-[var(--color-cream-faint)] focus:border-[var(--color-amber)]'

export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
