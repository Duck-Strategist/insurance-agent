import { splitLine, visibleHistory } from './answer'

test('加粗与引用标记；不在引用列表里的 [n] 当普通文字', () => {
  expect(splitLine('等待期**90 天**[1]，另见[3]。', new Set([1]))).toEqual([
    { kind: 'text', text: '等待期' },
    { kind: 'bold', text: '90 天' },
    { kind: 'cite', index: 1 },
    { kind: 'text', text: '，另见[3]。' },
  ])
})

test('没有标记时原样返回', () => {
  expect(splitLine('条款中没有找到明确约定', new Set())).toEqual([
    { kind: 'text', text: '条款中没有找到明确约定' },
  ])
  expect(splitLine('', new Set())).toEqual([])
})

test('流式进行中时去掉历史末尾属于这一轮的问答', () => {
  const q = { role: 'user' as const, content: '等待期多久' }
  const a = { role: 'assistant' as const, content: '90 天' }
  const old = [{ role: 'user' as const, content: '你好' }, { role: 'assistant' as const, content: '您好' }]
  expect(visibleHistory([...old, q], true, '等待期多久')).toEqual(old)
  expect(visibleHistory([...old, q, a], true, '等待期多久')).toEqual(old)
  expect(visibleHistory([...old, q, a], false, '等待期多久')).toHaveLength(4)
  // 同样内容的旧问题不在末尾时不动
  expect(visibleHistory([q, a, ...old], true, '等待期多久')).toHaveLength(4)
})
