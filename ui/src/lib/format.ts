export function money(usd: number): string {
  if (usd >= 100) return '$' + usd.toFixed(0)
  return '$' + usd.toFixed(2)
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

export function pad(n: number): string {
  return n < 10 ? '0' + n : String(n)
}

/** HH:MM from a server clock ms value. */
export function clockLabel(ms: number): string {
  const d = new Date(ms)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function clockSeconds(ms: number): string {
  const d = new Date(ms)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** Compact relative time, Thai. */
export function relTime(ts: number, now: number): string {
  const s = Math.max(0, Math.round((now - ts) / 1000))
  if (s < 5) return 'เมื่อกี้'
  if (s < 60) return `${s} วิ`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} นาที`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} ชม.`
  return `${Math.round(h / 24)} วัน`
}

export function compactNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(n)
}
