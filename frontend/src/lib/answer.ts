/**
 * 把回答正文切成片段：普通文字、**加粗**、引用标记 [n]。
 * 不引入 Markdown 库、不用 dangerouslySetInnerHTML：模型输出只按这三种格式渲染，其余原样显示。
 */

export type Segment =
  | { kind: 'text'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'cite'; index: number }

const TOKEN = /\*\*(.+?)\*\*|\[(\d{1,2})\]/g

/** validCites：本条回答实际引用的编号；不在其中的 [n]（模型编造的）按普通文字显示 */
export function splitLine(line: string, validCites: ReadonlySet<number>): Segment[] {
  const out: Segment[] = []
  let last = 0
  for (const m of line.matchAll(TOKEN)) {
    const start = m.index
    if (m[2] !== undefined && !validCites.has(Number(m[2]))) continue
    if (start > last) out.push({ kind: 'text', text: line.slice(last, start) })
    if (m[1] !== undefined) out.push({ kind: 'bold', text: m[1] })
    else out.push({ kind: 'cite', index: Number(m[2]) })
    last = start + m[0].length
  }
  if (last < line.length) out.push({ kind: 'text', text: line.slice(last) })
  return out
}

interface TurnMessage {
  role: 'user' | 'assistant'
  content: string
}

/**
 * 正在流式显示的这一轮，可能已经出现在从服务端拉到的历史里（提问一开始就写进了 checkpoint，
 * 回答完成后重新拉取时问答都在）。显示历史时去掉末尾属于这一轮的消息，避免同一条出现两次。
 */
export function visibleHistory<T extends TurnMessage>(
  history: T[],
  streaming: boolean,
  question: string,
): T[] {
  if (!streaming || history.length === 0) return history
  const last = history.length - 1
  if (history[last].role === 'user' && history[last].content === question) {
    return history.slice(0, last)
  }
  if (
    last >= 1 &&
    history[last].role === 'assistant' &&
    history[last - 1].role === 'user' &&
    history[last - 1].content === question
  ) {
    return history.slice(0, last - 1)
  }
  return history
}
