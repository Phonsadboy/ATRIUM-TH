import { withAlpha } from './primitives'

type ProblemDetailRow = {
  label: string
  value?: unknown
}

function cleanValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value.replace(/\s+/g, ' ').trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function problemRows(rows: ProblemDetailRow[]): { label: string; value: string }[] {
  return rows
    .map((row) => ({ label: row.label, value: cleanValue(row.value) }))
    .filter((row) => row.value)
}

export function ProblemDetails({
  summary = 'ดูรายละเอียดปัญหา',
  rows,
  color = '#f0735f',
  note,
  className = '',
}: {
  summary?: string
  rows: ProblemDetailRow[]
  color?: string
  note?: string
  className?: string
}) {
  const visibleRows = problemRows(rows)
  if (!visibleRows.length && !note) return null
  return (
    <details
      className={`group rounded-lg border text-[11px] ${className}`}
      style={{ borderColor: withAlpha(color, 0.32), background: withAlpha(color, 0.08) }}
      onClick={(event) => event.stopPropagation()}
    >
      <summary className="flex min-w-0 cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-[var(--color-cream-dim)] hover:text-[var(--color-cream)]">
        <span className="shrink-0 transition-transform group-open:rotate-90">▸</span>
        <span className="min-w-0 flex-1 truncate">{summary}</span>
      </summary>
      <div className="space-y-1.5 px-2.5 pb-2.5 leading-relaxed text-[var(--color-cream-dim)]">
        {note && <div className="text-[var(--color-cream-faint)]">{note}</div>}
        {visibleRows.map((row) => (
          <div key={row.label} className="grid min-w-0 grid-cols-[96px_minmax(0,1fr)] gap-2">
            <span className="text-[var(--color-cream-faint)]">{row.label}</span>
            <span className="min-w-0 break-words [overflow-wrap:anywhere]">{row.value}</span>
          </div>
        ))}
      </div>
    </details>
  )
}
