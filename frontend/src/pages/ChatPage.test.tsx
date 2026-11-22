import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import type { ChatMessage, Citation, PlanCardData } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { useChatStream } from '../stores/chat'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => {
  vi.unstubAllGlobals()
  useChatStream.getState().clearAll()
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

const plan: PlanCardData = {
  plan_id: 7,
  slots: { age: 30 },
  items: [
    {
      product_id: 4,
      product_name: '星相守2号',
      category: 'medical',
      category_label: '医疗险',
      params: { age: 30 },
      annual_premium: '184.00',
      pay_years: 1,
      total_premium: '184.00',
      sum_assured: null,
      rate_version: 'v1',
      lines: [{ title: '必选责任', premium: '184.00', basis: '费率表第 2 页：30 周岁', page: 2 }],
    },
    {
      product_id: 10,
      product_name: '小蜜蜂6号',
      category: 'accident',
      category_label: '意外险',
      params: { age: 30 },
      annual_premium: '136.00',
      pay_years: 1,
      total_premium: '136.00',
      sum_assured: null,
      rate_version: 'v2',
      lines: [{ title: '综合意外险（经典版）', premium: '136.00', basis: '第 1 页', page: 1 }],
    },
  ],
  dropped: [{ category: 'critical_illness', category_label: '重疾险', reason: '超出剩余预算' }],
  budget: '500.00',
  total_annual_premium: '320.00',
}

test('方案卡片：展示明细，「不要这个险种」走 resume 接口', async () => {
  useAuthStore.setState({ token: 't', user })
  const saved: ChatMessage[] = [
    { id: 'u1', role: 'user', content: '帮我配保险', citations: [] },
    {
      id: 'a1',
      role: 'assistant',
      content: '为您配置了医疗险和意外险。',
      citations: [],
      plan: { ...plan, status: 'pending' },
    },
  ]
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/threads/th-9/messages': () => jsonResponse(saved),
    '/api/v1/threads': () => jsonResponse([]),
    '/api/v1/plans': () => jsonResponse([]),
    '/api/v1/chat/resume': () => {
      // 服务端会把这一轮写进历史：去掉的那句话 + 回复
      saved.push(
        { id: 'u2', role: 'user', content: '不要：意外险', citations: [] },
        { id: 'a2', role: 'assistant', content: '方案已确认。', citations: [] },
      )
      return new Response(
        sseBody([
          ['plan_status', { plan_id: 7, status: 'confirmed' }],
          ['token', { text: '方案已确认。' }],
          ['done', {}],
        ]),
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    },
  })
  renderAt('/chat/th-9')

  expect(await screen.findByText('星相守2号')).toBeInTheDocument()
  expect(screen.getByText('待确认')).toBeInTheDocument()
  expect(screen.getByText(/重疾险：超出剩余预算/)).toBeInTheDocument()
  const editButton = screen.getByRole('button', { name: '按调整重新配置' })
  expect(editButton).toBeDisabled()

  // 意外险选「不要这个险种」
  const accident = screen.getByRole('radiogroup', { name: '调整小蜜蜂6号' })
  fireEvent.click(within(accident).getByRole('radio', { name: '不要这个险种' }))
  expect(editButton).toBeEnabled()
  fireEvent.click(editButton)
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/v1/chat/resume'))
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      thread_id: 'th-9',
      plan_id: 7,
      action: 'edit',
      remove_product_ids: [],
      remove_categories: ['accident'],
    })
  })
  await screen.findByText('方案已确认。')
  expect(screen.getByText('不要：意外险')).toBeInTheDocument()
})

test('非待确认的方案卡片没有操作按钮', async () => {
  useAuthStore.setState({ token: 't', user })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/threads/th-8/messages': () =>
      jsonResponse([
        {
          id: 'a1',
          role: 'assistant',
          content: '说明',
          citations: [],
          plan: { ...plan, status: 'superseded' },
        },
      ]),
    '/api/v1/threads': () => jsonResponse([]),
  })
  renderAt('/chat/th-8')
  expect(await screen.findByText('已调整')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认方案' })).not.toBeInTheDocument()
})

test('画像建议：一轮结束后仍显示，点保存才提交，提交的是服务端给的增量', async () => {
  useAuthStore.setState({ token: 't', user })
  const suggestion = {
    items: [
      { label: '年龄', value: '30 周岁（按年龄估算出生日期）' },
      { label: '城市', value: '杭州' },
    ],
    patch: { birth_date: '1996-05-24', birth_date_estimated: true, city: '杭州' },
  }
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/threads/th-5/messages': () => jsonResponse([]),
    '/api/v1/threads': () => jsonResponse([]),
    '/api/v1/profile/suggestions': () => jsonResponse({ city: '杭州' }),
    '/api/v1/chat': () =>
      new Response(
        sseBody([
          ['thread', { id: 'th-5', title: '我 30 岁住杭州' }],
          ['profile_suggestion', { suggestion }],
          ['token', { text: '好的。' }],
          ['done', {}],
        ]),
      ),
  })
  renderAt('/chat/th-5')
  const box = await screen.findByPlaceholderText(/输入问题/)
  fireEvent.change(box, { target: { value: '我 30 岁住杭州' } })
  fireEvent.click(screen.getByRole('button', { name: /发送/ }))

  expect(await screen.findByText('保存到画像？')).toBeInTheDocument()
  // 一轮结束（流式内容清空）后建议还在
  await waitFor(() => expect(useChatStream.getState().streaming).toBe(false))
  expect(screen.getByText('30 周岁（按年龄估算出生日期）')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/profile/suggestions'))).toBe(false)

  fireEvent.click(screen.getByRole('button', { name: '保存到画像' }))
  await waitFor(() => expect(screen.queryByText('保存到画像？')).not.toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/api/v1/profile/suggestions'))
  expect(call?.[1]?.method).toBe('POST')
  expect(JSON.parse(String(call?.[1]?.body))).toEqual(suggestion.patch)
})
