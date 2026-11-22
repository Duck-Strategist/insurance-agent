import { useQuery } from '@tanstack/react-query'
import { Alert, Flex, Table, Tag, Tooltip, Typography, type TableColumnsType } from 'antd'

import { adminApi } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import { SEARCH_MODE_LABELS, type EvalRun, type SearchMode } from '../lib/types'

const SUITE = 'retrieval'

const pct = (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—')
const ms = (v: number | undefined) => (v === undefined ? '—' : `${Math.round(v)}`)

const columns: TableColumnsType<EvalRun> = [
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
        {d.replace(/\.jsonl$/, '')}（{r.metrics.n ?? '?'} 题）
      </Tooltip>
    ),
  },
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
        </Flex>
      )
    },
  },
  { title: 'R@1', key: 'r1', width: 80, render: (_, r) => pct(r.metrics['recall@1']) },
  { title: 'R@5', key: 'r5', width: 80, render: (_, r) => pct(r.metrics['recall@5']) },
  {
    title: 'MRR@10',
    key: 'mrr',
    width: 90,
    render: (_, r) => (typeof r.metrics['mrr@10'] === 'number' ? r.metrics['mrr@10'].toFixed(3) : '—'),
  },
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

export function AdminEvalRunsPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminEvalRuns(SUITE),
    queryFn: () => adminApi.evalRuns(SUITE),
  })
  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        评测记录
      </Typography.Title>
      <Typography.Text type="secondary">
        检索评测由命令行运行（<Typography.Text code>uv run python -m app.eval retrieval</Typography.Text>
        ），每种配置一条记录。命中 = 前 k 个章节里有标准章节；延迟含向量化、召回与重排。
      </Typography.Text>
      {isError ? (
        <Alert type="error" showIcon message={error.message} />
      ) : (
        <Table<EvalRun>
          size="small"
          rowKey="id"
          loading={isPending}
          dataSource={data}
          columns={columns}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
        />
      )}
    </Flex>
  )
}
