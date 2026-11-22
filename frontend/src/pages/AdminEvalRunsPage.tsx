import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Flex,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from 'antd'
import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router'

import { adminApi } from '../lib/api'
import { E2E_FAILURE, INTENT_LABELS, num, pct, type E2EDetail } from '../lib/evals'
import { queryKeys } from '../lib/queryKeys'
import { SEARCH_MODE_LABELS, type EvalRun, type SearchMode } from '../lib/types'

type Suite = 'retrieval' | 'routing' | 'e2e'

const ms = (v: unknown) => (typeof v === 'number' ? `${Math.round(v)}` : '—')

const common: TableColumnsType<EvalRun> = [
  { title: '#', dataIndex: 'id', width: 60 },
  {
    title: '时间',
    dataIndex: 'created_at',
    width: 150,
    render: (t: string) => new Date(t).toLocaleString('zh-CN'),
  },
  {
    title: '评测集',
    dataIndex: 'dataset',
    render: (d: string, r) => (
      <Tooltip title={`内容哈希 ${r.dataset_hash.slice(0, 12)}`}>
        {d.replace(/\.jsonl$/, '')}（{String(r.metrics.n ?? '?')} 题）
      </Tooltip>
    ),
  },
]

const retrievalColumns: TableColumnsType<EvalRun> = [
  ...common,
  {
    title: '配置',
    key: 'config',
    render: (_, r) => {
      const c = r.config
      const mode = c.mode as SearchMode
      return (
        <Flex gap={4} wrap>
          <Tag color="blue">{SEARCH_MODE_LABELS[mode] ?? String(c.mode)}</Tag>
          {c.scope === 'global' && <Tag>不限条款</Tag>}
          {mode !== 'vector' && c.fulltext_weight !== undefined && c.fulltext_weight !== 1 && (
            <Tag>全文权重 {String(c.fulltext_weight)}</Tag>
          )}
          {mode === 'hybrid_rerank' && <Tag>{String(c.rerank_model)}</Tag>}
          {c.rewrite === true && <Tag color="purple">改写</Tag>}
        </Flex>
      )
    },
  },
  { title: 'R@1', key: 'r1', width: 80, render: (_, r) => pct(r.metrics['recall@1']) },
  { title: 'R@5', key: 'r5', width: 80, render: (_, r) => pct(r.metrics['recall@5']) },
  { title: 'MRR@10', key: 'mrr', width: 90, render: (_, r) => num(r.metrics['mrr@10']) },
  {
    title: '延迟 P50 / P95（ms）',
    key: 'lat',
    width: 160,
    render: (_, r) => {
      const t = r.metrics.latency_ms?.total
      return t ? `${ms(t.p50)} / ${ms(t.p95)}` : '—'
    },
  },
]

type Metrics = Record<string, unknown>
const sub = (m: Metrics, key: string): Metrics => (m[key] ?? {}) as Metrics

const routingColumns: TableColumnsType<EvalRun> = [
  ...common,
  { title: '模型', key: 'model', render: (_, r) => <Tag>{String(r.config.model ?? '—')}</Tag> },
  { title: '意图准确率', key: 'acc', width: 100, render: (_, r) => pct(r.metrics.accuracy) },
  { title: '宏 F1', key: 'f1', width: 80, render: (_, r) => num(r.metrics.macro_f1) },
  {
    title: '多轮准确率',
    key: 'mt',
    width: 100,
    render: (_, r) => pct(sub(r.metrics, 'multi_turn').accuracy),
  },
  { title: '产品识别', key: 'prod', width: 90, render: (_, r) => pct(r.metrics.product_accuracy) },
  { title: 'P50（ms）', key: 'lat', width: 90, render: (_, r) => ms(sub(r.metrics, 'latency_ms').p50) },
]

function HumanAgreement({ m }: { m: Metrics }) {
  const h = m.human as Metrics | undefined
  if (!h) return <Tag>待人工标注</Tag>
  return (
    <Tooltip
      title={`人工标注 ${String(h.n)}/${String(h.of)} 题；kappa ${num(h.correctness_kappa)}；拒答一致 ${pct(
        h.refused_agreement,
      )}；引用一致 ${pct(h.citations_agreement)}`}
    >
      {pct(h.correctness_agreement)}
    </Tooltip>
  )
}

const e2eColumns: TableColumnsType<EvalRun> = [
  ...common,
  {
    title: '回答 / 裁判',
    key: 'models',
    render: (_, r) => (
      <Flex gap={4} wrap>
        <Tag>{String(r.config.answer_model)}</Tag>
        <Tag color="gold">{String(r.config.judge_model)}</Tag>
      </Flex>
    ),
  },
  {
    title: '正确性',
    key: 'correct',
    width: 80,
    render: (_, r) => pct(sub(r.metrics, 'answerable').correctness),
  },
  { title: '引用忠实度', key: 'cite', width: 100, render: (_, r) => pct(r.metrics.citation_precision) },
  {
    title: '拒答率（无答案题）',
    key: 'refuse',
    width: 140,
    render: (_, r) => pct(sub(r.metrics, 'unanswerable').refusal_rate),
  },
  {
    title: '误拒率',
    key: 'false',
    width: 80,
    render: (_, r) => pct(sub(r.metrics, 'answerable').false_refusal_rate),
  },
  {
    title: '裁判与人工一致率',
    key: 'human',
    width: 140,
    render: (_, r) => <HumanAgreement m={r.metrics} />,
  },
]

const INTENTS = Object.keys(INTENT_LABELS)

/** 路由：混淆矩阵 + 各意图分数（都在 metrics 里，不用再请求逐题结果） */
function RoutingDetail({ run }: { run: EvalRun }) {
  const confusion = (run.metrics.confusion ?? {}) as Record<string, Record<string, number>>
  const scores = (run.metrics.per_intent ?? {}) as Record<string, Metrics>
  const rows = INTENTS.map((g) => ({ key: g, gold: g, ...confusion[g] }))
  return (
    <Flex gap={24} wrap>
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        title={() => '混淆矩阵（行：标准，列：预测）'}
        columns={[
          { title: '', dataIndex: 'gold', render: (g: string) => <b>{INTENT_LABELS[g]}</b> },
          ...INTENTS.map((p) => ({
            title: INTENT_LABELS[p],
            dataIndex: p,
            render: (v: number, row: { gold: string }) =>
              v ? (
                <span style={{ color: row.gold === p ? '#389e0d' : '#cf1322', fontWeight: 600 }}>{v}</span>
              ) : (
                <Typography.Text type="secondary">0</Typography.Text>
              ),
          })),
        ]}
      />
      <Table
        size="small"
        pagination={false}
        title={() => '各意图'}
        dataSource={INTENTS.map((k) => ({ key: k, intent: k, ...scores[k] }))}
        columns={[
          { title: '意图', dataIndex: 'intent', render: (k: string) => INTENT_LABELS[k] },
          { title: '精确率', dataIndex: 'precision', render: num },
          { title: '召回率', dataIndex: 'recall', render: num },
          { title: 'F1', dataIndex: 'f1', render: num },
          { title: '题数', dataIndex: 'support' },
        ]}
      />
    </Flex>
  )
}

/** 端到端：失败案例（逐题结果单独请求，列表接口不带） */
function E2EDetailView({ run }: { run: EvalRun }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminEvalRun(run.id),
    queryFn: () => adminApi.evalRun(run.id),
  })
  if (isError) return <Alert type="error" showIcon message={error.message} />
  const failures = ((data?.details ?? []) as E2EDetail[]).filter(E2E_FAILURE)
  return (
    <Table<E2EDetail>
      size="small"
      rowKey="id"
      loading={isPending}
      dataSource={failures}
      title={() => `失败案例（${failures.length}）：答错、部分正确、该答没答、无答案却编造、引用没有依据`}
      pagination={{ pageSize: 10, hideOnSinglePage: true }}
      columns={[
        { title: '题目', dataIndex: 'id', width: 130 },
        {
          title: '问题',
          dataIndex: 'question',
          render: (q: string, d) => (
            <>
              {q} {!d.answerable && <Tag>无答案题</Tag>}
            </>
          ),
        },
        {
          title: '裁判',
          key: 'judge',
          width: 220,
          render: (_, d) => {
            const bad = Object.entries(d.judge.citations).filter(([, ok]) => !ok)
            return (
              <Flex gap={4} wrap>
                <Tag color={d.judge.correctness === 'correct' ? 'green' : d.judge.correctness === 'partial' ? 'orange' : 'red'}>
                  {d.judge.correctness}
                </Tag>
                {d.judge.refused && <Tag>拒答</Tag>}
                {bad.length > 0 && <Tag color="red">无依据引用 {bad.map(([k]) => `[${k}]`).join('')}</Tag>}
              </Flex>
            )
          },
        },
        { title: '理由', key: 'reason', render: (_, d) => d.judge.reason },
      ]}
      expandable={{
        expandedRowRender: (d) => (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{d.answer}</Typography.Paragraph>
        ),
      }}
    />
  )
}

const SUITES: Record<Suite, { label: string; columns: TableColumnsType<EvalRun>; help: ReactNode; detail?: (r: EvalRun) => ReactNode }> = {
  retrieval: {
    label: '检索',
    columns: retrievalColumns,
    help: (
      <>
        <Typography.Text code>uv run python -m app.eval retrieval</Typography.Text>
        ：每种配置一条记录。命中 = 前 k 个章节里有标准章节；延迟含向量化、召回与重排。
      </>
    ),
  },
  routing: {
    label: '路由',
    columns: routingColumns,
    help: (
      <>
        <Typography.Text code>uv run python -m app.eval routing</Typography.Text>
        ：默认跑测试集，调提示词时只看开发集（routing_dev）。产品识别只统计条款问答和理赔题。展开一行看混淆矩阵。
      </>
    ),
    detail: (r) => <RoutingDetail run={r} />,
  },
  e2e: {
    label: '端到端',
    columns: e2eColumns,
    help: (
      <>
        <Typography.Text code>uv run python -m app.eval e2e</Typography.Text>
        ：线上同一套检索 + 回答，另一个模型当裁判。正确性 correct 记 1、partial 记 0.5；
        人工抽检 20% 后用 <Typography.Text code>agreement</Typography.Text> 命令写入一致率。展开一行看失败案例。
      </>
    ),
    detail: (r) => <E2EDetailView run={r} />,
  },
}

function SuiteRuns({ suite }: { suite: Suite }) {
  const conf = SUITES[suite]
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminEvalRuns(suite),
    queryFn: () => adminApi.evalRuns(suite),
  })
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">{conf.help}</Typography.Text>
      {isError ? (
        <Alert type="error" showIcon message={error.message} />
      ) : (
        <Table<EvalRun>
          size="small"
          rowKey="id"
          loading={isPending}
          dataSource={data}
          columns={conf.columns}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          expandable={conf.detail ? { expandedRowRender: conf.detail } : undefined}
        />
      )}
    </Flex>
  )
}

export function AdminEvalRunsPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('suite')
  const suite: Suite = raw === 'routing' || raw === 'e2e' ? raw : 'retrieval'
  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        评测记录
      </Typography.Title>
      <Tabs
        activeKey={suite}
        onChange={(k) => setParams(k === 'retrieval' ? {} : { suite: k }, { replace: true })}
        items={(Object.keys(SUITES) as Suite[]).map((k) => ({
          key: k,
          label: SUITES[k].label,
          children: <SuiteRuns suite={k} />,
        }))}
      />
    </Flex>
  )
}
