import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { E2E_FAILURE, type E2EDetail } from '../lib/evals'
import type { EvalRun } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const admin = { id: 1, username: 'boss', role: 'admin' as const }

const routingRun: EvalRun = {
  id: 143,
  suite: 'routing',
  dataset: 'routing_test.jsonl',
  dataset_hash: 'abcdef0123456789',
  config: { model: 'qwen-plus' },
  metrics: {
    n: 203,
    accuracy: 0.956,
    macro_f1: 0.952,
    multi_turn: { n: 55, accuracy: 0.964 },
    product_accuracy: 0.991,
    latency_ms: { p50: 1115.8, p95: 1895.9 } as unknown as EvalRun["metrics"]["latency_ms"],
    confusion: {
      clause_qa: { clause_qa: 64, advisor: 0, claim: 2, chitchat: 0, other: 0 },
      advisor: { clause_qa: 1, advisor: 43, claim: 0, chitchat: 0, other: 0 },
      claim: { clause_qa: 2, advisor: 0, claim: 41, chitchat: 0, other: 0 },
      chitchat: { clause_qa: 0, advisor: 0, claim: 0, chitchat: 23, other: 0 },
      other: { clause_qa: 0, advisor: 2, claim: 0, chitchat: 2, other: 23 },
    },
    per_intent: {},
  },
  created_at: '2026-11-22T15:08:00Z',
}

const detail = (over: Partial<E2EDetail>): E2EDetail => ({
  id: 'e2e-1',
  question: '等待期多久',
  answerable: true,
  answer: '等待期 90 天[1]',
  cited_sections: [],
  judge: { refused: false, correctness: 'correct', citations: { '1': true }, reason: '一致', parsed: true },
  no_hits: false,
  latency_ms: 1000,
  ...over,
})

const e2eRun: EvalRun = {
  id: 150,
  suite: 'e2e',
  dataset: 'e2e_clause.jsonl',
  dataset_hash: 'ffff',
  config: { answer_model: 'qwen-plus', judge_model: 'qwen-max' },
  metrics: {
    n: 86,
    answerable: { correctness: 0.85, false_refusal_rate: 0.05 },
    citation_precision: 0.93,
    unanswerable: { refusal_rate: 0.917 },
  },
  created_at: '2026-11-22T15:30:00Z',
}

test('失败案例的判断：答错、误拒、编造、引用无依据都算', () => {
  expect(E2E_FAILURE(detail({}))).toBe(false)
  expect(E2E_FAILURE(detail({ judge: { ...detail({}).judge, correctness: 'partial' } }))).toBe(true)
  expect(E2E_FAILURE(detail({ judge: { ...detail({}).judge, refused: true } }))).toBe(true)
  expect(E2E_FAILURE(detail({ judge: { ...detail({}).judge, citations: { '1': false } } }))).toBe(true)
  // 无答案题：拒答且没编造才算对
  const none = detail({ answerable: false, judge: { ...detail({}).judge, refused: true, citations: {} } })
  expect(E2E_FAILURE(none)).toBe(false)
  expect(E2E_FAILURE({ ...none, judge: { ...none.judge, refused: false } })).toBe(true)
})

test('路由页签：指标与混淆矩阵；端到端页签：待人工标注与失败案例', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/eval-runs/150': () =>
      jsonResponse({
        ...e2eRun,
        details: [
          detail({ id: 'e2e-ok' }),
          detail({ id: 'e2e-bad', question: '犹豫期多久', judge: { ...detail({}).judge, correctness: 'incorrect', reason: '天数错了' } }),
        ],
      }),
    '/api/v1/admin/eval-runs': (url) =>
      jsonResponse(url.searchParams.get('suite') === 'e2e' ? [e2eRun] : [routingRun]),
  })
  const { router } = renderAt('/admin/evals?suite=routing')

  expect(await screen.findByText('95.6%')).toBeInTheDocument()
  expect(screen.getByText('96.4%')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('suite=routing'))).toBe(true)
  fireEvent.click(screen.getAllByRole('button', { name: /展开行|Expand row/ })[0])
  expect(await screen.findByText('混淆矩阵（行：标准，列：预测）')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: '端到端' }))
  await waitFor(() => expect(router.state.location.search).toBe('?suite=e2e'))
  expect(await screen.findByText('待人工标注')).toBeInTheDocument()
  expect(screen.getByText('qwen-max')).toBeInTheDocument()
  const rows = screen.getAllByRole('button', { name: /展开行|Expand row/ })
  fireEvent.click(rows[rows.length - 1])
  const failures = await screen.findByText(/失败案例（1）/)
  const table = failures.closest('.ant-table-wrapper') as HTMLElement
  expect(within(table).getByText('犹豫期多久')).toBeInTheDocument()
  expect(within(table).getByText('天数错了')).toBeInTheDocument()
  expect(within(table).queryByText('e2e-ok')).not.toBeInTheDocument()
})
