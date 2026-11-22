/**
 * 条款原文里的 HTML 片段（MinerU 输出的表格、上下标）和 $...$ 公式解析成结构化数据，由组件渲染。
 *
 * 只认 table/tr/td/th/sup/sub 六种标签，其余尖括号内容一律当普通文字；不用 innerHTML，
 * 条款来自上传的 PDF，不能把原文当 HTML 执行。只改显示，入库原文不动（spec D28、Q26）。
 * 命中片段可能在表格或标签中间被截断：没闭合的表格按已有的行显示，没闭合的上下标延续到结尾。
 */

export type InlineKind = 'text' | 'sup' | 'sub'
export interface Inline {
  kind: InlineKind
  text: string
}
export interface Cell {
  parts: Inline[]
  header: boolean
  rowSpan?: number
  colSpan?: number
}
export type Block = { kind: 'inline'; parts: Inline[] } | { kind: 'table'; rows: Cell[][] }

const TAG = /<(\/?)(table|tr|td|th|sup|sub)\b([^<>]*)>/gi

function span(attrs: string, name: string): number | undefined {
  const m = new RegExp(`${name}\\s*=\\s*["']?(\\d+)`, 'i').exec(attrs)
  const n = m ? Number(m[1]) : NaN
  return n > 1 && n <= 100 ? n : undefined
}

function append(parts: Inline[], kind: InlineKind, text: string) {
  if (!text) return
  const last = parts[parts.length - 1]
  if (last && last.kind === kind) last.text += text
  else parts.push({ kind, text })
}

// MinerU 把公式写成 $...$。条款里出现的只有上下标、字体包装和少数符号（spec D63），
// 认识的转成文字和上下标；有不认识的命令就原样显示，不猜。
const MATH = /\$([^$\n]{1,160})\$/g
const MATH_WRAPPERS = /\\(?:mathrm|mathsf|mathbf|mathit|text|operatorname|left|right)\b/g
const MATH_SYMBOLS: Record<string, string> = {
  times: '×',
  cdot: '·',
  cdots: '⋯',
  colon: '：',
  geqslant: '≥',
  geq: '≥',
  ge: '≥',
  leqslant: '≤',
  leq: '≤',
  le: '≤',
  pm: '±',
  sim: '~',
  alpha: 'α',
  beta: 'β',
  gamma: 'γ',
  mu: 'μ',
}

/** 把一段 LaTeX 公式转成文字和上下标；不认识就返回 null。 */
export function mathToInline(src: string): Inline[] | null {
  // 没有任何公式记号的不当公式（比如两处美元金额之间的文字），否则会被去掉空格
  if (!/[\\^_{]/.test(src)) return null
  let s = src.replace(MATH_WRAPPERS, '').replace(/\\([%,;! ])/g, (_, c: string) => (c === '%' ? '%' : ''))
  let unknown = false
  s = s.replace(/\\([a-zA-Z]+)/g, (_, name: string) => {
    const sym = MATH_SYMBOLS[name]
    if (sym === undefined) unknown = true
    return sym ?? ''
  })
  if (unknown) return null
  s = s.replace(/\s+/g, '')
  const out: Inline[] = []
  let i = 0
  // 上下标的对象：一个字符，或一对花括号里的全部内容（内层的括号和上下标记号去掉）
  const atom = (): string | null => {
    if (s[i] !== '{') return s[i++] ?? null
    let depth = 0
    for (let j = i; j < s.length; j++) {
      if (s[j] === '{') depth++
      else if (s[j] === '}' && --depth === 0) {
        const inner = s.slice(i + 1, j).replace(/[{}^_]/g, '')
        i = j + 1
        return inner
      }
    }
    return null
  }
  while (i < s.length) {
    const c = s[i]
    if (c === '{' || c === '}') {
      i++
    } else if (c === '^' || c === '_') {
      i++
      const t = atom()
      if (t === null) return null
      append(out, c === '^' ? 'sup' : 'sub', t)
    } else {
      append(out, 'text', c)
      i++
    }
  }
  return out
}

/** 追加一段原文，其中的 $...$ 公式转成上下标；已经在上下标里时公式压成同一种。 */
function appendSource(parts: Inline[], mode: InlineKind, text: string) {
  let pos = 0
  for (const m of text.matchAll(MATH)) {
    const math = mathToInline(m[1])
    if (!math) continue
    append(parts, mode, text.slice(pos, m.index))
    math.forEach((p) => append(parts, mode === 'text' ? p.kind : mode, p.text))
    pos = m.index + m[0].length
  }
  append(parts, mode, text.slice(pos))
}

function trimNewlines(parts: Inline[], side: 'start' | 'end') {
  if (side === 'start') {
    while (parts.length && parts[0].kind === 'text') {
      parts[0].text = parts[0].text.replace(/^\n+/, '')
      if (parts[0].text) break
      parts.shift()
    }
  } else {
    while (parts.length && parts[parts.length - 1].kind === 'text') {
      const last = parts[parts.length - 1]
      last.text = last.text.replace(/\n+$/, '')
      if (last.text) break
      parts.pop()
    }
  }
}

export function parseClause(source: string): Block[] {
  const blocks: Block[] = []
  let inline: Inline[] = []
  let mode: InlineKind = 'text'
  let rows: Cell[][] | null = null
  let row: Cell[] | null = null
  let cell: Cell | null = null
  let afterTable = false

  const flushInline = (beforeTable: boolean) => {
    if (afterTable) trimNewlines(inline, 'start')
    if (beforeTable) trimNewlines(inline, 'end')
    if (inline.length) blocks.push({ kind: 'inline', parts: inline })
    inline = []
    afterTable = false
  }
  const closeCell = () => {
    if (cell) {
      if (!row) {
        row = []
        rows?.push(row)
      }
      row.push(cell)
    }
    cell = null
  }
  const closeRow = () => {
    closeCell()
    row = null
  }
  const closeTable = () => {
    closeRow()
    if (rows && rows.length) {
      blocks.push({ kind: 'table', rows })
      afterTable = true
    }
    rows = null
  }
  const text = (t: string) => {
    if (!rows) return appendSource(inline, mode, t)
    if (!cell) {
      if (!t.trim()) return // 标签之间的换行、空格
      cell = { parts: [], header: false }
    }
    appendSource(cell.parts, mode, t)
  }

  let pos = 0
  for (const m of source.matchAll(TAG)) {
    text(source.slice(pos, m.index))
    pos = m.index + m[0].length
    const closing = m[1] === '/'
    const tag = m[2].toLowerCase()
    if (tag === 'sup' || tag === 'sub') {
      mode = closing ? 'text' : tag
    } else if (tag === 'table') {
      if (closing) closeTable()
      else {
        closeTable()
        flushInline(true)
        rows = []
      }
    } else if (!rows) {
      // 表格外的 tr/td 不成结构，丢掉标签本身
    } else if (tag === 'tr') {
      closeRow()
      if (!closing) {
        row = []
        rows.push(row)
      }
    } else if (closing) {
      closeCell()
    } else {
      closeCell()
      cell = { parts: [], header: tag === 'th', rowSpan: span(m[3], 'rowspan'), colSpan: span(m[3], 'colspan') }
    }
  }
  text(source.slice(pos))
  closeTable()
  flushInline(false)
  return blocks
}

/** 片段预览用：表格压成「单元格 | 单元格」一行一行的文字，保留上下标。 */
export function flattenTables(blocks: Block[]): Inline[] {
  const out: Inline[] = []
  blocks.forEach((b, i) => {
    if (i > 0) append(out, 'text', '\n')
    if (b.kind === 'inline') {
      b.parts.forEach((p) => append(out, p.kind, p.text))
      return
    }
    b.rows.forEach((r, ri) => {
      if (ri > 0) append(out, 'text', '\n')
      r.forEach((c, ci) => {
        if (ci > 0) append(out, 'text', ' | ')
        c.parts.forEach((p) => append(out, p.kind, p.text))
      })
    })
  })
  return out
}

/** 去掉标签后的字数（显示用）。 */
export function clauseLength(source: string): number {
  const count = (parts: Inline[]) => parts.reduce((n, p) => n + p.text.length, 0)
  return parseClause(source).reduce(
    (n, b) =>
      n + (b.kind === 'inline' ? count(b.parts) : b.rows.flat().reduce((m, c) => m + count(c.parts), 0)),
    0,
  )
}
