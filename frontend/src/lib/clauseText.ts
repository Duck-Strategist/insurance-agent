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
const MATH_MAX = 160
const MATH_WRAPPERS = /\\(?:mathrm|mathsf|mathbf|mathit|operatorname|left|right)\b/g
// \text{...} 里是普通文字，空格要保留（公式其余部分的空格是 MinerU 的排版，要去掉）
const MATH_TEXT = /\\text\s*\{([^{}]*)\}/g
const KEEP_SPACE = '\u0000'
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
  let s = src
    .replace(MATH_TEXT, (_, inner: string) => `{${inner.trim().replace(/\s+/g, KEEP_SPACE)}}`)
    .replace(MATH_WRAPPERS, '')
    .replace(/\\([%,;! ])/g, (_, c: string) => (c === '%' ? '%' : ''))
  let unknown = false
  s = s.replace(/\\([a-zA-Z]+)/g, (_, name: string) => {
    const sym = MATH_SYMBOLS[name]
    if (sym === undefined) unknown = true
    return sym ?? ''
  })
  if (unknown) return null
  s = s.replace(/\s+/g, '').replaceAll(KEEP_SPACE, ' ')
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
  let pos = 0 // 已输出到这里
  let open = text.indexOf('$')
  while (open !== -1) {
    const close = text.indexOf('$', open + 1)
    if (close === -1) break
    const body = text.slice(open + 1, close)
    const math = body.length <= MATH_MAX && !body.includes('\n') ? mathToInline(body) : null
    if (!math) {
      // 这一对不是公式（如两处美元金额）：第二个 $ 可能是下一个公式的开头，从它重新配对
      open = close
      continue
    }
    append(parts, mode, text.slice(pos, open))
    math.forEach((p) => append(parts, mode === 'text' ? p.kind : mode, p.text))
    pos = close + 1
    open = text.indexOf('$', pos)
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
  // 嵌套表格：进入内层前把外层的状态压栈，内层结束后压成文字放回外层的单元格
  const outer: { rows: Cell[][]; row: Cell[] | null; cell: Cell }[] = []

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
    // 没闭合的上下标不跨单元格、行、表格延续（只在原文末尾被截断时才延续到结尾）
    mode = 'text'
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
  /** 结束内层表格：压成「单元格 | 单元格」的文字行，接在外层当前单元格后面。 */
  const closeInner = () => {
    closeRow()
    const inner = flattenTables([{ kind: 'table', rows: rows ?? [] }])
    const parent = outer.pop()!
    rows = parent.rows
    row = parent.row
    cell = parent.cell
    if (cell.parts.length && inner.length) append(cell.parts, 'text', '\n')
    for (const p of inner) append(cell.parts, p.kind, p.text)
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
      if (!closing && rows) {
        outer.push({ rows, row, cell: cell ?? { parts: [], header: false } })
        rows = []
        row = null
        cell = null
        mode = 'text'
      } else if (closing && outer.length) {
        closeInner()
      } else if (closing) {
        closeTable()
      } else {
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
  // 截断在内层表格里：把内层压回外层
  while (outer.length) closeInner()
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
