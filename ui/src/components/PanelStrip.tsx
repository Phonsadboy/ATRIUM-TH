/**
 * A thin rail shown in place of a collapsed side panel: an expand button plus a
 * vertical label. Both are clickable so the whole strip reads as "click to open".
 */
export function PanelStrip({
  label,
  dir,
  onExpand,
}: {
  label: string
  /** which way the panel opens — controls the chevron direction */
  dir: 'open-left' | 'open-right'
  onExpand: () => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center gap-3 py-3">
      <button
        type="button"
        onClick={onExpand}
        title={`ขยาย${label}`}
        aria-label={`ขยาย${label}`}
        className="flex h-8 w-8 items-center justify-center rounded-lg border text-[16px] leading-none text-[var(--color-cream-dim)] transition-colors hover:border-[var(--color-line)] hover:text-[var(--color-cream)]"
        style={{ borderColor: 'var(--color-line-soft)' }}
      >
        {dir === 'open-right' ? '›' : '‹'}
      </button>
      <button
        type="button"
        onClick={onExpand}
        className="select-none text-[11px] font-semibold tracking-[0.18em] text-[var(--color-cream-faint)] uppercase transition-colors hover:text-[var(--color-cream-dim)]"
        style={{ writingMode: 'vertical-rl' }}
      >
        {label}
      </button>
    </div>
  )
}
