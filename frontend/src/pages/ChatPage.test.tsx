import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import type { ChatMessage, Citation } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { useChatStream } from '../stores/chat'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => {
  vi.unstubAllGlobals()
  useChatStream.getState().reset()
})

const user = { id: 1, username: 'alice', role: 'user' as const }

const citation: Citation = {
  index: 1,
  document_id: 3,
  doc_title: '众安尊享e生百万医疗险2026版条款',
  section_path: ['第七部分 释义', '四、等待期'],
  snippet: '等待期指……',
  parent_content: '四、等待期\n\n指自本合同生效日起的一段时间。',
}

function sseBody(events: [string, unknown][]) {
  const text = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
  const bytes = new TextEncoder().encode(text)
  // 分成两块发，模拟流式到达
  return new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(bytes.slice(0, 40))
      c.enqueue(bytes.slice(40))
      c.close()
    },
  })
}

test('未登录访问对话页跳到登录页', async () => {
  mockFetch({ '/health': healthOk })
  const { router } = renderAt('/chat')
  await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
})

test('新对话：流式显示回答，跳到新会话地址，点击引用展开条款原文', async () => {
  useAuthStore.setState({ token: 't', user })
  let saved: ChatMessage[] = []
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/threads/th-1/messages': () => jsonResponse(saved),
    '/api/v1/threads': () => jsonResponse([]),
    '/api/v1/chat': () => {
      saved = [
        { id: 'u1', role: 'user', content: '等待期多久？', citations: [] },
        { id: 'a1', role: 'assistant', content: '等待期为 **90 天**[1]。', citations: [citation] },
      ]
      return new Response(
        sseBody([
          ['thread', { id: 'th-1', title: '等待期多久？' }],
          ['status', { text: '正在检索条款…' }],
          ['token', { text: '等待期为 **90 天**' }],
          ['token', { text: '[1]。' }],
          ['citations', { items: [citation] }],
          ['done', {}],
        ]),
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    },
  })
  const { router } = renderAt('/chat')

  const box = await screen.findByPlaceholderText(/输入问题/)
  fireEvent.change(box, { target: { value: '等待期多久？' } })
  fireEvent.click(screen.getByRole('button', { name: /发送/ }))

  await waitFor(() => expect(router.state.location.pathname).toBe('/chat/th-1'))
  expect(await screen.findByText('90 天')).toBeInTheDocument()
  // 请求体：新对话不带 thread_id
  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/v1/chat'))
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ message: '等待期多久？' })

  // 正文里的 [1] 渲染成可点击的标记，点开后展示章节原文
  fireEvent.click(await screen.findByRole('button', { name: '查看出处 1' }))
  expect(await screen.findByText(/指自本合同生效日起的一段时间/)).toBeInTheDocument()
  expect(screen.getByText(/第七部分 释义 › 四、等待期/)).toBeInTheDocument()
})

test('后端返回 error 事件时显示错误，不显示半截回答', async () => {
  useAuthStore.setState({ token: 't', user })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/threads/th-9/messages': () => jsonResponse([]),
    '/api/v1/threads': () => jsonResponse([{ id: 'th-9', title: '旧对话', created_at: '', updated_at: '' }]),
    '/api/v1/chat': () =>
      new Response(sseBody([['thread', { id: 'th-9', title: '旧对话' }], ['error', { message: '回答生成失败，请稍后重试' }]])),
  })
  renderAt('/chat/th-9')
  const list = await screen.findByText('旧对话')
  expect(within(list.closest('li')!).getByText('旧对话')).toBeInTheDocument()
  const box = await screen.findByPlaceholderText(/输入问题/)
  fireEvent.change(box, { target: { value: '再问一次' } })
  fireEvent.click(screen.getByRole('button', { name: /发送/ }))
  expect(await screen.findByText('回答生成失败，请稍后重试')).toBeInTheDocument()
})
