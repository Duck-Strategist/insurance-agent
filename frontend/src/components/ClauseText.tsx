import { theme } from 'antd'
import { Fragment, useMemo } from 'react'

import { flattenTables, parseClause, type Inline } from '../lib/clauseText'

function Parts({ parts }: { parts: Inline[] }) {
  return parts.map((p, i) => {
    if (p.kind === 'sup') return <sup key={i}>{p.text}</sup>
    if (p.kind === 'sub') return <sub key={i}>{p.text}</sub>
    return <Fragment key={i}>{p.text}</Fragment>
  })
}

/**
 * 条款原文：表格画成表格、上下标画成上下标，其余按原文（含换行）显示。
 * `flat` 用于片段预览，表格压成文字行，便于省略号截断。
 */
export function ClauseText({ text, flat = false }: { text: string; flat?: boolean }) {
  const { token } = theme.useToken()
  const blocks = useMemo(() => parseClause(text), [text])
  if (flat) return <Parts parts={flattenTables(blocks)} />
  const border = `1px solid ${token.colorBorderSecondary}`
  return blocks.map((b, i) =>
    b.kind === 'inline' ? (
      <Fragment key={i}>
        <Parts parts={b.parts} />
      </Fragment>
    ) : (
      <div key={i} style={{ overflowX: 'auto', margin: '6px 0' }}>
        <table style={{ borderCollapse: 'collapse', whiteSpace: 'pre-wrap', fontSize: 'inherit' }}>
          <tbody>
            {b.rows.map((r, ri) => (
              <tr key={ri}>
                {r.map((c, ci) => {
                  const Cell = c.header ? 'th' : 'td'
                  // 编号、短标题列不折行，否则「1.1」会被挤成一字一行
                  const short = c.parts.reduce((n, p) => n + p.text.length, 0) <= 8
                  return (
                    <Cell
                      key={ci}
                      rowSpan={c.rowSpan}
                      colSpan={c.colSpan}
                      style={{
                        border,
                        padding: '4px 8px',
                        verticalAlign: 'top',
                        textAlign: 'left',
                        whiteSpace: short ? 'nowrap' : undefined,
                      }}
                    >
                      <Parts parts={c.parts} />
                    </Cell>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
  )
}
