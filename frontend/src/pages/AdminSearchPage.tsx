import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Card,
  Collapse,
  Empty,
  Flex,
  Input,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useSearchParams } from 'react-router'

import { adminApi } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import { SEARCH_MODE_LABELS, type SearchHit, type SearchMode } from '../lib/types'

const MODE_OPTIONS = Object.entries(SEARCH_MODE_LABELS).map(([value, label]) => ({
  value: value as SearchMode,
  label,
}))

const TIMING_LABELS: Record<string, string> = {
  embed_ms: '向量化',
  recall_ms: '召回',
  rerank_ms: '重排',
  total_ms: '合计',
}

function parseMode(raw: string | null): SearchMode {
  return raw && raw in SEARCH_MODE_LABELS ? (raw as SearchMode) : 'hybrid_rerank'
}

function parseDocId(raw: string | null): number | undefined {
  const n = Number(raw)
  return raw && Number.isInteger(n) && n > 0 ? n : undefined
}

function HitCard({ hit, rank }: { hit: SearchHit; rank: number }) {
  return (
    <Card size="small">
      <Flex vertical gap={6}>
        <Space size={[6, 4]} wrap>
          <Tag color="blue">#{rank}</Tag>
          <Typography.Text strong>{hit.doc_title}</Typography.Text>
          <Typography.Text type="secondary">{hit.section_path.join(' › ') || '（无标题）'}</Typography.Text>
          <Tag>分数 {hit.score.toFixed(3)}</Tag>
        </Space>
        <Typography.Paragraph
          style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
          ellipsis={{ rows: 3, expandable: 'collapsible' }}
        >
          {hit.snippet}
        </Typography.Paragraph>
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: 'parent',
              label: `章节原文（${hit.parent_content.length} 字）`,
              children: (
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  {hit.parent_content}
                </Typography.Paragraph>
              ),
            },
          ]}
        />
      </Flex>
    </Card>
  )
}

export function AdminSearchPage() {
  // 查询、模式和条款放在 URL 里：调试结果可以直接把链接发给别人复现
  const [params, setParams] = useSearchParams()
  const query = (params.get('q') ?? '').trim()
  const mode = parseMode(params.get('mode'))
  const docId = parseDocId(params.get('doc'))

  const update = (patch: Record<string, string | undefined>) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v)
        else next.delete(k)
      }
      return next
    })

  const docs = useQuery({
    queryKey: queryKeys.adminDocuments,
    queryFn: adminApi.listDocuments,
  })
  const result = useQuery({
    queryKey: queryKeys.adminSearch(query, mode, docId),
    queryFn: () => adminApi.search({ query, mode, document_id: docId }),
    enabled: query.length > 0,
    // 每次检索都会调用付费的向量化/重排接口：同一查询不自动重发
    staleTime: Infinity,
    retry: false,
  })

  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        检索调试
      </Typography.Title>
      <Typography.Text type="secondary">
        对比三种检索配置的结果与耗时。返回的是章节（父块）级结果，同一章节只保留得分最高的片段。
      </Typography.Text>
      <Flex gap={12} wrap>
        <Input.Search
          key={query}
          defaultValue={query}
          placeholder="输入问题，例如：等待期多久？"
          enterButton="检索"
          maxLength={200}
          allowClear
          style={{ flex: '1 1 360px' }}
          onSearch={(v) => update({ q: v.trim() || undefined })}
        />
        <Select
          allowClear
          placeholder="不限条款"
          style={{ flex: '0 1 320px' }}
          loading={docs.isPending}
          value={docId}
          onChange={(v?: number) => update({ doc: v ? String(v) : undefined })}
          options={docs.data
            ?.filter((d) => d.searchable)
            .map((d) => ({ value: d.id, label: d.file_name.replace(/\.pdf$/i, '') }))}
        />
      </Flex>
      <Segmented<SearchMode>
        options={MODE_OPTIONS}
        value={mode}
        onChange={(v) => update({ mode: v })}
      />
      {!query ? (
        <Empty description="输入问题开始检索" />
      ) : result.isError ? (
        <Alert type="error" showIcon message={result.error.message} />
      ) : result.isPending ? (
        <Skeleton active />
      ) : (
        <Flex vertical gap={12}>
          <Space size={[6, 4]} wrap>
            {Object.entries(result.data.timings).map(([k, v]) => (
              <Tag key={k} color={k === 'total_ms' ? 'geekblue' : undefined}>
                {TIMING_LABELS[k] ?? k} {Math.round(v)} ms
              </Tag>
            ))}
          </Space>
          {result.data.hits.length === 0 ? (
            <Empty description="没有检索到相关章节" />
          ) : (
            result.data.hits.map((h, i) => <HitCard key={h.parent_id} hit={h} rank={i + 1} />)
          )}
        </Flex>
      )}
    </Flex>
  )
}
