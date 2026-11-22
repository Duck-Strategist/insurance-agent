import { clauseLength, flattenTables, mathToInline, parseClause } from './clauseText'

// 片段取自真实入库的条款（MinerU 输出）
test('上下标：TNM 分期与脚注编号', () => {
  expect(parseClause('TNM分期为 T<sub>1</sub>N<sub>0</sub>M<sub>0</sub>期')).toEqual([
    {
      kind: 'inline',
      parts: [
        { kind: 'text', text: 'TNM分期为 T' },
        { kind: 'sub', text: '1' },
        { kind: 'text', text: 'N' },
        { kind: 'sub', text: '0' },
        { kind: 'text', text: 'M' },
        { kind: 'sub', text: '0' },
        { kind: 'text', text: '期' },
      ],
    },
  ])
  expect(parseClause('（9） 既往症<sup>61</sup>（被保险人')).toEqual([
    {
      kind: 'inline',
      parts: [
        { kind: 'text', text: '（9） 既往症' },
        { kind: 'sup', text: '61' },
        { kind: 'text', text: '（被保险人' },
      ],
    },
  ])
})

test('表格：前后文字、合并单元格、单元格里的上标', () => {
  const blocks = parseClause(
    '1 合同订立\n\n<table><tr><td>1.1</td><td colspan="2">合同构成</td></tr>\n' +
      '<tr><td rowspan="2">2</td><td>保额<sup>1</sup></td><th>说明</th></tr></table>\n\n后文',
  )
  expect(blocks).toEqual([
    { kind: 'inline', parts: [{ kind: 'text', text: '1 合同订立' }] },
    {
      kind: 'table',
      rows: [
        [
          { parts: [{ kind: 'text', text: '1.1' }], header: false, rowSpan: undefined, colSpan: undefined },
          { parts: [{ kind: 'text', text: '合同构成' }], header: false, rowSpan: undefined, colSpan: 2 },
        ],
        [
          { parts: [{ kind: 'text', text: '2' }], header: false, rowSpan: 2, colSpan: undefined },
          {
            parts: [
              { kind: 'text', text: '保额' },
              { kind: 'sup', text: '1' },
            ],
            header: false,
            rowSpan: undefined,
            colSpan: undefined,
          },
          { parts: [{ kind: 'text', text: '说明' }], header: true, rowSpan: undefined, colSpan: undefined },
        ],
      ],
    },
    { kind: 'inline', parts: [{ kind: 'text', text: '后文' }] },
  ])
  expect(clauseLength('1 合同订立<table><tr><td>1.1</td><td>合同构成</td></tr></table>')).toBe(13)
})

test('命中片段被截断：没闭合的表格和上标照样显示', () => {
  const blocks = parseClause('<table><tr><td>2.1</td><td>基本保险金额</td></tr><tr><td>2.2</td><td>保险期')
  expect(blocks).toHaveLength(1)
  expect(blocks[0].kind === 'table' && blocks[0].rows.map((r) => r.length)).toEqual([2, 2])
  expect(parseClause('甲状腺癌<sup>12')).toEqual([
    {
      kind: 'inline',
      parts: [
        { kind: 'text', text: '甲状腺癌' },
        { kind: 'sup', text: '12' },
      ],
    },
  ])
})

test('其他标签和尖括号原样当文字，不当 HTML', () => {
  const src = '<script>alert(1)</script><img src=x onerror=1>年龄<18岁</td>'
  expect(parseClause(src)).toEqual([
    { kind: 'inline', parts: [{ kind: 'text', text: '<script>alert(1)</script><img src=x onerror=1>年龄<18岁' }] },
  ])
})

test('片段预览把表格压成文字行', () => {
  const blocks = parseClause('前文<table><tr><td>1.1</td><td>合同构成</td></tr><tr><td>1.2</td><td>生效</td></tr></table>')
  expect(flattenTables(blocks)).toEqual([{ kind: 'text', text: '前文\n1.1 | 合同构成\n1.2 | 生效' }])
})

// 公式片段取自真实入库的条款（MinerU 的 $...$ 输出，spec D63）
test('公式：上下标、字体包装与符号', () => {
  const flat = (s: string) => mathToInline(s)?.map((p) => (p.kind === 'text' ? p.text : `${p.kind}(${p.text})`)).join('')
  expect(flat('\\mathrm { p T } _ { \\mathrm { 3 a } } \\colon')).toBe('pTsub(3a)：')
  expect(flat('\\mathrm { { T _ { 1 a } } }')).toBe('Tsub(1a)')
  expect(flat('{ < } 2 0 \\times 1 0 ^ { 9 } / \\mathrm { L }')).toBe('<20×10sup(9)/L')
  expect(flat('FEV_1\\%')).toBe('FEVsub(1)%')
  expect(flat('(1+1.25\\%)^{(n-1)}')).toBe('(1+1.25%)sup((n-1))')
  expect(flat('\\geqslant 50mmHg')).toBe('≥50mmHg')
  // 不认识的命令、括号不配对：原样显示
  expect(mathToInline('\\frac{1}{2}')).toBeNull()
  expect(mathToInline('x^{2')).toBeNull()
  // 没有公式记号：不是公式（如 $100 和 $200 之间的文字）
  expect(mathToInline('100 和 ')).toBeNull()
  // \text{} 里的空格保留，其余空格去掉
  expect(flat('\\text{per year} \\times 2')).toBe('per year×2')
  expect(flat('\\text { mmHg } _ { 2 }')).toBe('mmHgsub(2)')
})

test('不是公式的一对 $ 不会吞掉后面公式的开头', () => {
  expect(parseClause('费用 $5 起，指标 $PaO_2$ 偏低')).toEqual([
    {
      kind: 'inline',
      parts: [
        { kind: 'text', text: '费用 $5 起，指标 PaO' },
        { kind: 'sub', text: '2' },
        { kind: 'text', text: ' 偏低' },
      ],
    },
  ])
  expect(parseClause('只有一个 $ 号')).toEqual([{ kind: 'inline', parts: [{ kind: 'text', text: '只有一个 $ 号' }] }])
})

test('没闭合的上下标不跨单元格和表格延续', () => {
  const blocks = parseClause('<table><tr><td>保额<sup>1</td><td>说明</td></tr></table>后文')
  expect(blocks[0].kind === 'table' && blocks[0].rows[0].map((c) => c.parts)).toEqual([
    [
      { kind: 'text', text: '保额' },
      { kind: 'sup', text: '1' },
    ],
    [{ kind: 'text', text: '说明' }],
  ])
  expect(blocks[1]).toEqual({ kind: 'inline', parts: [{ kind: 'text', text: '后文' }] })
})

test('嵌套表格压成文字放回外层单元格，外层结构不断', () => {
  const blocks = parseClause(
    '<table><tr><td>A<table><tr><td>x</td><td>y</td></tr></table></td><td>B</td></tr><tr><td>C</td></tr></table>',
  )
  expect(blocks).toHaveLength(1)
  expect(blocks[0].kind === 'table' && blocks[0].rows.map((r) => r.map((c) => c.parts.map((p) => p.text).join('')))).toEqual([
    ['A\nx | y', 'B'],
    ['C'],
  ])
  // 截断在内层表格里
  const cut = parseClause('<table><tr><td>A<table><tr><td>x')
  expect(cut[0].kind === 'table' && cut[0].rows[0][0].parts).toEqual([{ kind: 'text', text: 'A\nx' }])
})

test('原文里的公式就地转换，转不了的保留 $', () => {
  expect(parseClause('动脉血氧分压$PaO_2<60mmHg$，见$\\frac{1}{2}$')).toEqual([
    {
      kind: 'inline',
      parts: [
        { kind: 'text', text: '动脉血氧分压PaO' },
        { kind: 'sub', text: '2' },
        { kind: 'text', text: '<60mmHg，见$\\frac{1}{2}$' },
      ],
    },
  ])
  // 表格单元格里的脚注
  const [table] = parseClause('<table><tr><td>保单年度 $^{1}$</td></tr></table>')
  expect(table.kind === 'table' && table.rows[0][0].parts).toEqual([
    { kind: 'text', text: '保单年度 ' },
    { kind: 'sup', text: '1' },
  ])
})
