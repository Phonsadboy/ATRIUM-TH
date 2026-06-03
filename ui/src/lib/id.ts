let counter = 0

export function uid(prefix = 'id'): string {
  counter += 1
  return `${prefix}_${counter.toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}
