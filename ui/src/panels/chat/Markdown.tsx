import { useState, type ReactNode } from 'react'
import { withAlpha } from '../../components/primitives'

/* ------------------------------------------------------------------ *
 * Lightweight, dependency-free Markdown renderer for chat bubbles.
 * Renders to React nodes (never dangerouslySetInnerHTML, so no XSS).
 * Supports: fenced code (+copy), headings, ul/ol lists, blockquotes,
 * hr, GFM tables (alignment + h-scroll), **bold**, *italic*, `code`,
 * [links](url), and bare URLs.
 * ------------------------------------------------------------------ */

let keySeq = 0
const k = () => `md_${(keySeq += 1)}`

const WORD = 'A-Za-z0-9'
const INLINE = new RegExp(
  [
    '`[^`]+`',
    '\\*\\*[^*]+\\*\\*',
    `(?<![${WORD}])__[^_]+__(?![${WORD}])`,
    '\\*[^*\\n]+\\*',
    `(?<![${WORD}])_[^_\\n]+_(?![${WORD}])`,
    '\\[[^\\]]+\\]\\([^)\\s]+\\)',
    'https?:\\/\\/[^\\s<>()]+',
  ].join('|'),
  'g',
)

const ALLOWED_LINK_PROTOCOLS = new Set(['http', 'https', 'mailto'])

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(INLINE)) {
    const tok = m[0]
    const start = m.index ?? 0
    if (start > last) out.push(text.slice(last, start))
    last = start + tok.length
    if (tok.startsWith('`')) {
      out.push(
        <code
          key={k()}
          className="rounded px-1 py-0.5 text-[12px]"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-line-soft)', fontFamily: 'var(--font-mono)' }}
        >
          {tok.slice(1, -1)}
        </code>,
      )
    } else if (tok.startsWith('**') || tok.startsWith('__')) {
      out.push(<strong key={k()}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*') || tok.startsWith('_')) {
      out.push(<em key={k()}>{tok.slice(1, -1)}</em>)
    } else if (tok.startsWith('[')) {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)
      if (mm) out.push(<Anchor key={k()} href={mm[2]} label={mm[1]} />)
      else out.push(tok)
    } else if (tok.startsWith('http')) {
      out.push(<Anchor key={k()} href={tok} label={tok} />)
    } else {
      out.push(tok)
    }
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function safeLinkHref(href: string): string | null {
  const value = href.trim()
  if (!value || hasControlChar(value)) return null
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(value)?.[1]?.toLowerCase()
  if (scheme && !ALLOWED_LINK_PROTOCOLS.has(scheme)) return null
  return value
}

function hasControlChar(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i)
    if (code < 32 || code === 127) return true
  }
  return false
}

function Anchor({ href, label }: { href: string; label: string }) {
  const safeHref = safeLinkHref(href)
  if (!safeHref) return <span>{label}</span>
  return (
    <a
      href={safeHref}
      target="_blank"
      rel="noreferrer noopener"
      className="underline decoration-dotted underline-offset-2"
      style={{ color: 'var(--color-sky, #7eb0dc)' }}
    >
      {label}
    </a>
  )
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard
      ?.writeText(code)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1200)
      })
      .catch(() => undefined)
  }
  return (
    <div
      className="my-1.5 overflow-hidden rounded-xl"
      style={{ border: '1px solid var(--color-line-soft)' }}
    >
      <div
        className="flex items-center justify-between gap-2 px-3 py-1 text-[10px] uppercase tracking-wide"
        style={{ color: 'var(--color-cream-faint)', background: 'var(--color-surface)', borderBottom: '1px solid var(--color-line-soft)' }}
      >
        <span style={{ fontFamily: 'var(--font-mono)' }}>{lang || 'code'}</span>
        <button
          type="button"
          onClick={copy}
          className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 normal-case transition-colors hover:bg-[var(--color-surface-3)]"
          style={{ color: copied ? 'var(--color-teal)' : 'var(--color-cream-dim)' }}
          title="คัดลอกโค้ดทั้งบล็อก"
        >
          {copied ? '✓ คัดลอกแล้ว' : '⧉ คัดลอก'}
        </button>
      </div>
      <pre
        className="overflow-x-auto px-3 py-2.5 text-[12.5px] leading-relaxed"
        style={{ background: 'var(--color-ink)', fontFamily: 'var(--font-mono)' }}
      >
        <code style={{ fontFamily: 'var(--font-mono)' }}>{code}</code>
      </pre>
    </div>
  )
}

type Align = 'left' | 'right' | 'center' | null

type Block =
  | { t: 'code'; code: string; lang?: string }
  | { t: 'h'; level: number; text: string }
  | { t: 'ul'; items: string[] }
  | { t: 'ol'; items: string[]; start: number }
  | { t: 'quote'; text: string }
  | { t: 'hr' }
  | { t: 'table'; header: string[]; align: Align[]; rows: string[][] }
  | { t: 'p'; text: string }

/* --- GFM table helpers ------------------------------------------------ *
 * A table is a row of `|`-delimited cells whose NEXT line is a delimiter
 * row (cells of dashes, optional leading/trailing `:` for alignment). We
 * detect on that header+delimiter pair so stray pipes in prose never trip
 * it. Rows are kept rectangular to the header width (ragged rows padded). */

// Split one row into trimmed cells, honouring escaped pipes (`\|`) and
// dropping the empty edge cells produced by leading/trailing `|`.
function splitRow(line: string): string[] {
  const cells: string[] = []
  let cur = ''
  for (let j = 0; j < line.length; j += 1) {
    const ch = line[j]
    if (ch === '\\' && line[j + 1] === '|') { cur += '|'; j += 1; continue }
    if (ch === '|') { cells.push(cur); cur = ''; continue }
    cur += ch
  }
  cells.push(cur)
  if (cells.length > 1 && cells[0].trim() === '') cells.shift()
  if (cells.length > 1 && cells[cells.length - 1].trim() === '') cells.pop()
  return cells.map((c) => c.trim())
}

const DELIM_CELL = /^:?-+:?$/
function isDelimiterRow(line: string): boolean {
  if (!line.includes('-') || !line.includes('|')) return false
  const cells = splitRow(line)
  return cells.length > 0 && cells.every((c) => DELIM_CELL.test(c))
}

function cellAlign(c: string): Align {
  const l = c.startsWith(':')
  const r = c.endsWith(':')
  if (l && r) return 'center'
  if (r) return 'right'
  if (l) return 'left'
  return null
}

// If a table starts at line i (header + delimiter), parse it and return the
// index to resume at; else null.
function tableAt(lines: string[], i: number): { header: string[]; align: Align[]; rows: string[][]; next: number } | null {
  const head = lines[i]
  if (!head || !head.includes('|') || i + 1 >= lines.length) return null
  if (!isDelimiterRow(lines[i + 1])) return null
  const header = splitRow(head)
  const align = splitRow(lines[i + 1]).map(cellAlign)
  const rows: string[][] = []
  let j = i + 2
  while (j < lines.length && lines[j].includes('|') && !/^```/.test(lines[j])) {
    rows.push(splitRow(lines[j]))
    j += 1
  }
  return { header, align, rows, next: j }
}

function MdTable({ header, align, rows }: { header: string[]; align: Align[]; rows: string[][] }) {
  // The wrapper is the h-scroll escape hatch: capped at the bubble width
  // (max-w-full), it scrolls a wide table internally instead of stretching the
  // bubble. Cells wrap (min-w-0 / overflow-wrap), so a 2-col table fits without
  // scrolling at all. min-width keeps columns readable before scroll kicks in.
  const align0 = (j: number) => align[j] ?? 'left'
  return (
    <div
      className="my-1.5 max-w-full overflow-x-auto rounded-xl"
      style={{ border: '1px solid var(--color-line-soft)' }}
    >
      <table className="w-full border-collapse text-[13px] leading-[1.5]">
        <thead>
          <tr>
            {header.map((h, j) => (
              <th
                key={k()}
                className="break-words [overflow-wrap:anywhere] px-2.5 py-1.5 align-top font-semibold"
                style={{
                  minWidth: '4.5rem',
                  textAlign: align0(j),
                  background: 'var(--color-surface)',
                  borderBottom: '1px solid var(--color-line-soft)',
                }}
              >
                {renderInline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={k()} style={ri % 2 ? { background: withAlpha('#aba090', 0.05) } : undefined}>
              {header.map((_, j) => (
                <td
                  key={k()}
                  className="break-words [overflow-wrap:anywhere] px-2.5 py-1.5 align-top"
                  style={{
                    minWidth: '4.5rem',
                    textAlign: align0(j),
                    borderTop: '1px solid var(--color-line-soft)',
                  }}
                >
                  {renderInline(r[j] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// A real ordered-list item: "1. text" or "1) text" (captures the number).
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/
// A standalone *bold* numbered line like "**1. ตลับคลาสสิก**". Models often
// write a numbered list this way and mis-number every item "1.". Treat it as an
// ordered item so we renumber it. Dot-only ("1.") on purpose, so a section
// header written as "**2) หัวข้อ**" (paren) stays a plain bold paragraph.
const BOLD_ORDERED = /^\s*\*\*\s*(\d+)\.\s+(.+?)\*\*\s*$/

/** If a line is an ordered item (plain or bold), return its literal number +
 *  inner text (bold items keep their `**…**` so they render bold); else null. */
function orderedItem(line: string): { n: number; text: string } | null {
  const b = BOLD_ORDERED.exec(line)
  if (b) return { n: Number(b[1]), text: `**${b[2].trim()}**` }
  if (/^\s*\*/.test(line)) return null
  const o = ORDERED.exec(line)
  return o ? { n: Number(o[1]), text: o[2] } : null
}

function parse(src: string): Block[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  // Running ordered-list numbering. Models frequently mis-number every item as
  // "1." and split one logical list with explanatory paragraphs or bullet
  // sub-lists, which naively renders as "1. 1. 1.". So we keep ONE running
  // counter across blank lines, paragraphs, and bullets, and only:
  //   • reset at a structural section break (heading / hr / code / quote), or
  //   • reset when a list clearly restarts — the previous list was numbered
  //     properly (its last number > 1) and the next list begins again at 1.
  let orderedSeq = 0
  let lastOrderedN = 0
  const resetOrdered = () => {
    orderedSeq = 0
    lastOrderedN = 0
  }
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // fenced code
    const fence = /^```(\w+)?\s*$/.exec(line)
    if (fence) {
      const lang = fence[1]
      const buf: string[] = []
      i += 1
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++])
      i += 1 // closing fence
      blocks.push({ t: 'code', code: buf.join('\n'), lang })
      resetOrdered()
      continue
    }
    if (/^\s*$/.test(line)) { i += 1; continue }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) { blocks.push({ t: 'h', level: h[1].length, text: h[2] }); i += 1; resetOrdered(); continue }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { blocks.push({ t: 'hr' }); i += 1; resetOrdered(); continue }
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''))
      blocks.push({ t: 'quote', text: buf.join(' ') })
      resetOrdered()
      continue
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*+]\s+/, ''))
      blocks.push({ t: 'ul', items })
      // no reset — a bullet sub-list is usually content under a numbered item
      continue
    }
    const tbl = tableAt(lines, i)
    if (tbl) {
      blocks.push({ t: 'table', header: tbl.header, align: tbl.align, rows: tbl.rows })
      i = tbl.next
      resetOrdered()
      continue
    }
    const first = orderedItem(line)
    if (first) {
      const run = [first]
      i += 1
      for (let next = i < lines.length ? orderedItem(lines[i]) : null; next; ) {
        run.push(next)
        i += 1
        next = i < lines.length ? orderedItem(lines[i]) : null
      }
      // properly-numbered previous list that has ended, then a fresh "1." → a
      // genuinely new list; otherwise continue the running count
      if (orderedSeq > 0 && first.n === 1 && lastOrderedN > 1) orderedSeq = 0
      const start = orderedSeq + 1
      orderedSeq += run.length
      lastOrderedN = run[run.length - 1].n
      blocks.push({ t: 'ol', items: run.map((r) => r.text), start })
      continue
    }
    // paragraph: gather consecutive plain lines. Does NOT reset numbering, so a
    // list interrupted by an explanatory line keeps counting.
    const buf: string[] = []
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,4})\s/.test(lines[i]) &&
      !/^\s*>/.test(lines[i]) &&
      !/^\s*[-*+]\s/.test(lines[i]) &&
      orderedItem(lines[i]) === null &&
      tableAt(lines, i) === null
    ) buf.push(lines[i++])
    blocks.push({ t: 'p', text: buf.join('\n') })
  }
  return blocks
}

export function Markdown({ text }: { text: string }) {
  const blocks = parse(text)
  return (
    <div className="space-y-1.5">
      {blocks.map((b) => {
        switch (b.t) {
          case 'code':
            return <CodeBlock key={k()} code={b.code} lang={b.lang} />
          case 'h':
            return (
              <div
                key={k()}
                className="font-semibold"
                style={{ fontSize: b.level <= 1 ? 16 : b.level === 2 ? 15 : 14 }}
              >
                {renderInline(b.text)}
              </div>
            )
          case 'hr':
            return <hr key={k()} style={{ borderColor: 'var(--color-line-soft)' }} />
          case 'table':
            return <MdTable key={k()} header={b.header} align={b.align} rows={b.rows} />
          case 'quote':
            return (
              <blockquote
                key={k()}
                className="border-l-2 pl-2.5 text-[14px] italic"
                style={{ borderColor: withAlpha('#aba090', 0.5), color: 'var(--color-cream-faint)' }}
              >
                {renderInline(b.text)}
              </blockquote>
            )
          case 'ul':
            return (
              <ul key={k()} className="list-disc space-y-1 pl-5 text-[14px] leading-[1.6] marker:text-[var(--color-cream-faint)]">
                {b.items.map((it) => <li key={k()}>{renderInline(it)}</li>)}
              </ul>
            )
          case 'ol':
            return (
              <ol key={k()} start={b.start} className="list-decimal space-y-1 pl-5 text-[14px] leading-[1.6] marker:text-[var(--color-cream-faint)]">
                {b.items.map((it) => <li key={k()}>{renderInline(it)}</li>)}
              </ol>
            )
          default:
            return (
              <p key={k()} className="whitespace-pre-wrap text-[14px] leading-[1.65]">
                {renderInline(b.text)}
              </p>
            )
        }
      })}
    </div>
  )
}
