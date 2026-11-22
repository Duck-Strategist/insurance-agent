import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
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
import { useLayoutEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'

import { ClauseText } from '../components/ClauseText'
import { adminApi } from '../lib/api'
import { clauseLength } from '../lib/clauseText'
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
  load_ms: '取章节',
  total_ms: '合计',
}

function parseMode(raw: string | null): SearchMode {
  return raw && raw in SEARCH_MODE_LABELS ? (raw as SearchMode) : 'hybrid_rerank'
}

function parseDocId(raw: string | null): number | undefined {
  const n = Number(raw)
  return raw && Number.isInteger(n) && n > 0 ? n : undefined
}

/**
 * 命中片段，默认显示 3 行。antd 的 ellipsis 在子元素不是纯字符串时（上下标）会截成空白，
 * 所以用 CSS 行数截断，自己判断是否溢出（spec Q26）。
 */
function Snippet({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [overflow, setOverflow] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || open) return
    const measure = () => setOverflow(el.scrollHeight > el.clientHeight + 1)
    measure()
    // 窗口变窄、字体加载完后行数会变，重新判断要不要显示「展开」
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [text, open])
  const clamp = open
    ? {}
    : { display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' as const, overflow: 'hidden' }
  return (
    <div>
      <div ref={ref} style={{ whiteSpace: 'pre-wrap', ...clamp }}>
        <ClauseText text={text.trim()} flat />
      </div>
      {(overflow || open) && (
        <Button
          type="link"
          size="small"
          style={{ padding: 0, height: 'auto' }}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? '收起' : '展开'}
        </Button>
      )}
    </div>
  )
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
        <Snippet text={hit.snippet} />
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: 'parent',
              label: `章节原文（${clauseLength(hit.parent_content)} 字）`,
              children: (
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  <ClauseText text={hit.parent_content} />
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
