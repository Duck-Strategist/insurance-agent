import { fireEvent, screen, waitFor } from '@testing-library/react'

import type { EvalRun, SearchResponse } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const admin = { id: 1, username: 'boss', role: 'admin' as const }

const result: SearchResponse = {
  hits: [
    {
      parent_id: 11,
      child_id: 12,
      document_id: 3,
      doc_title: '众安尊享e生百万医疗险2026版条款',
      section_path: ['第七部分 释义', '四、等待期'],
      snippet: '指自本合同生效日零时起计算的一段时间',
      parent_content: '四、等待期\n\n指自本合同生效日零时起……',
      score: 0.782,
    },
  ],
  timings: { embed_ms: 512.3, recall_ms: 88.1, rerank_ms: 950.4, total_ms: 1550.8 },
}

test('URL 里的查询、模式和条款会原样发给后端，结果展示章节路径与耗时', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () => jsonResponse([]),
    '/api/v1/admin/search': () => jsonResponse(result),
  })
  renderAt('/admin/search?q=等待期多久&mode=hybrid&doc=3')

  expect(await screen.findByText('第七部分 释义 › 四、等待期')).toBeInTheDocument()
  expect(screen.getByText('合计 1551 ms')).toBeInTheDocument()
  expect(screen.getByText('分数 0.782')).toBeInTheDocument()

  const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/admin/search'))
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    query: '等待期多久',
    mode: 'hybrid',
    document_id: 3,
  })
})

test('没有输入问题时不调用检索接口', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () => jsonResponse([]),
  })
  renderAt('/admin/search?mode=bogus')
  expect(await screen.findByText('输入问题开始检索')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/admin/search'))).toBe(false)
})

test('评测记录页展示配置与指标', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const run: EvalRun = {
    id: 3,
    suite: 'retrieval',
    dataset: 'retrieval_hard.jsonl',
    dataset_hash: 'abcdef1234567890',
    config: { mode: 'hybrid_rerank', scope: 'document', fulltext_weight: 0.5, rerank_model: 'gte-rerank-v2' },
    metrics: {
      n: 34,
      'recall@1': 0.5,
      'recall@5': 0.794,
      'mrr@10': 0.619,
      latency_ms: { total: { p50: 2047.3, p95: 2984.4 }, embed: {}, recall: {}, rerank: {} },
    },
    created_at: '2026-11-21T10:58:32Z',
  }
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/eval-runs': () => jsonResponse([run]),
  })
  renderAt('/admin/evals')
  expect(await screen.findByText('retrieval_hard（34 题）')).toBeInTheDocument()
  expect(screen.getByText('全文权重 0.5')).toBeInTheDocument()
  expect(screen.getByText('79.4%')).toBeInTheDocument()
  expect(screen.getByText('2047 / 2984')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('gte-rerank-v2')).toBeInTheDocument())
})

test('条款原文里的表格和上标按结构显示，不露出标签（spec Q26）', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const hit = {
    ...result.hits[0],
    snippet: '（9） 既往症<sup>61</sup>（被保险人已如实告知）',
    parent_content: '1 合同订立\n<table><tr><td>1.1</td><td>合同构成</td></tr></table>',
  }
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () => jsonResponse([]),
    '/api/v1/admin/search': () => jsonResponse({ ...result, hits: [hit] }),
  })
  const { container } = renderAt('/admin/search?q=既往症')

  expect(await screen.findByText('61')).toHaveProperty('tagName', 'SUP')
  fireEvent.click(screen.getByText('章节原文（13 字）'))
  expect(await screen.findByRole('cell', { name: '合同构成' })).toBeInTheDocument()
  expect(container.textContent).not.toMatch(/<\/?(sup|table|td)/)
})
