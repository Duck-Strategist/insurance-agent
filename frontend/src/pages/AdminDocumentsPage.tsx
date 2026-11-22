import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Flex,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  type TableColumnsType,
} from 'antd'
import { useState } from 'react'

import { adminApi } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import type { ClauseDocument, DocumentStatus, ParentPreview } from '../lib/types'

const STATUS: Record<DocumentStatus, { color: string; label: string }> = {
  pending: { color: 'default', label: '待入库' },
  parsed: { color: 'processing', label: '已解析' },
  indexed: { color: 'success', label: '已入库' },
  failed: { color: 'error', label: '失败' },
}

const REMOVED_LABELS: Record<string, string> = {
  repeated_header_footer: '页眉页脚',
  page_number: '页码',
  toc: '目录行',
  toc_block: '目录块',
  image: '图片',
  joined_broken_lines: '接断行',
  split_inline_headings: '拆双栏',
  inconsistent_parent_dropped: '修正父级',
  dec1_as_list_item: '列表项',
}

// 编号类型 → 示例写法
const HEADING_LABELS: Record<string, string> = {
  part: '第X部分',
  article: '第X条',
  cn_paren: '（一）',
  cn_dun: '一、',
  num_paren: '（1）',
  dec1: '1.',
  dec2: '1.1',
  dec3: '1.1.1',
  dec4: '1.1.1.1',
}

const PARENTS_PAGE_SIZE = 20

/** 停在 pending/parsed 却不在处理中：入库被打断了（如服务重启），需要重新入库 */
function isInterrupted(d: ClauseDocument) {
  return !d.processing && (d.status === 'pending' || d.status === 'parsed')
}

function StatusCell({ d }: { d: ClauseDocument }) {
  if (d.processing) return <Tag color="processing">入库中…</Tag>
  const tags = []
  if (isInterrupted(d)) {
    tags.push(
      <Tooltip key="s" title="入库过程被打断（如服务重启），请点「重新入库」">
        <Tag color="warning">入库中断</Tag>
      </Tooltip>,
    )
  } else {
    tags.push(
      <Tooltip key="s" title={d.error}>
        <Tag color={STATUS[d.status].color}>{STATUS[d.status].label}</Tag>
      </Tooltip>,
    )
  }
  // 最近一次操作失败或未完成时，说明检索实际在用什么
  if (d.status !== 'indexed') {
    tags.push(
      d.searchable ? (
        <Tag key="v" color={d.stale ? 'orange' : 'blue'}>
          {d.stale ? '检索使用旧版本' : '检索可用'}
        </Tag>
      ) : (
        <Tag key="v">不可检索</Tag>
      ),
    )
  }
  return (
    <Space size={[4, 4]} wrap>
      {tags}
    </Space>
  )
}

function ParentsTable({ docId }: { docId: number }) {
  const [page, setPage] = useState(1)
  // 服务端分页：一份条款可能有几百个父块（众安 293 个），不能一次只取前 N 条
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminParents(docId, page),
    queryFn: () =>
      adminApi.parents(docId, (page - 1) * PARENTS_PAGE_SIZE, PARENTS_PAGE_SIZE),
    placeholderData: keepPreviousData,
  })
  if (isError) return <Alert type="error" message={error.message} />
  return (
    <Table<ParentPreview>
      size="small"
      rowKey="id"
      loading={isPending}
      dataSource={data?.items}
      pagination={{
        current: page,
        pageSize: PARENTS_PAGE_SIZE,
        total: data?.total ?? 0,
        hideOnSinglePage: true,
        showSizeChanger: false,
        showTotal: (total) => `共 ${total} 个父块`,
        onChange: setPage,
      }}
      columns={[
        { title: '#', dataIndex: 'seq', width: 60 },
        {
          title: '章节路径',
          dataIndex: 'section_path',
          render: (p: string[]) => p.join(' › ') || '（无标题）',
        },
        { title: '字数', dataIndex: 'chars', width: 80 },
        { title: '子块', dataIndex: 'child_count', width: 70 },
        {
          title: '内容预览',
          dataIndex: 'preview',
          ellipsis: { showTitle: false },
          render: (t: string) => (
            <Tooltip title={t} placement="topLeft">
              {t}
            </Tooltip>
          ),
        },
      ]}
    />
  )
}

export function AdminDocumentsPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminDocuments,
    queryFn: adminApi.listDocuments,
    // 只在后台确实有文档在入库时轮询；停在 pending/parsed 的（入库被打断）不轮询，否则永远停不下来
    refetchInterval: (q) => (q.state.data?.some((d) => d.processing) ? 3000 : false),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.adminDocuments })

  const reindex = useMutation({
    mutationFn: adminApi.reindex,
    onSuccess: () => {
      message.success('已开始重新入库')
      void refresh()
    },
    onError: (e) => message.error(e.message),
  })

  const upload = useMutation({
    mutationFn: adminApi.upload,
    onSuccess: (doc) => {
      message.success(`${doc.file_name} 已上传，开始入库`)
      void refresh()
    },
    onError: (e) => message.error(e.message),
  })

  const columns: TableColumnsType<ClauseDocument> = [
    {
      title: '条款文件',
      dataIndex: 'file_name',
      render: (name: string, d) => (
        <Flex vertical gap={4}>
          <Typography.Text strong>{name}</Typography.Text>
          <Space size={[4, 4]} wrap>
            {d.products.length === 0 ? (
              <Typography.Text type="warning">未关联产品</Typography.Text>
            ) : (
              d.products.map((p) => <Tag key={p.id}>{p.name}</Tag>)
            )}
          </Space>
        </Flex>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 150,
      render: (_, d) => <StatusCell d={d} />,
    },
    {
      title: '父块 / 子块',
      width: 110,
      // 计数描述的是库里现有的块（可能是旧版本），只要可检索就显示
      render: (_, d) => (d.searchable ? `${d.parent_count} / ${d.child_count}` : '—'),
    },
    {
      title: '标题识别',
      width: 200,
      render: (_, d) => {
        const levels = d.stats.levels
        if (!levels) return '—'
        const order = Object.entries(levels).sort((a, b) => a[1] - b[1])
        const label = (k: string) => `${HEADING_LABELS[k] ?? k}×${d.stats.headings?.[k] ?? 0}`
        // 最高一级是「第X部分」时，各部分内部的编号层级分别分配，全文统一的 L 层级没有意义
        const text = d.stats.levels_per_part
          ? `${label('part')} · 部分内各自分级：${order
              .filter(([k]) => k !== 'part')
              .map(([k]) => label(k))
              .join('、')}`
          : order.map(([k, lv]) => `L${lv} ${label(k)}`).join(' · ')
        return (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {text}
          </Typography.Text>
        )
      },
    },
    {
      title: '清洗删除',
      width: 160,
      render: (_, d) => {
        const removed = d.stats.removed_lines
        if (!removed || Object.keys(removed).length === 0) return '—'
        return (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {Object.entries(removed)
              .map(([k, n]) => `${REMOVED_LABELS[k] ?? k} ${n}`)
              .join('，')}
          </Typography.Text>
        )
      },
    },
    {
      title: '入库时间',
      dataIndex: 'indexed_at',
      width: 170,
      render: (t: string | null) => (t ? new Date(t).toLocaleString('zh-CN') : '—'),
    },
    {
      title: '操作',
      width: 110,
      render: (_, d) => (
        <Button
          size="small"
          icon={<ReloadOutlined />}
          disabled={d.processing}
          loading={reindex.isPending && reindex.variables === d.id}
          onClick={() => reindex.mutate(d.id)}
        >
          重新入库
        </Button>
      ),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          条款管理
        </Typography.Title>
        <Upload
          accept=".pdf,application/pdf"
          showUploadList={false}
          beforeUpload={(file) => {
            upload.mutate(file)
            return false // 不用 antd 自带的上传，走统一的 request()（带 token、统一报错）
          }}
        >
          <Button type="primary" icon={<UploadOutlined />} loading={upload.isPending}>
            上传条款 PDF
          </Button>
        </Upload>
      </Flex>
      {isError ? (
        <Alert type="error" showIcon message="加载失败" description={error.message} />
      ) : (
        <Table<ClauseDocument>
          rowKey="id"
          loading={isPending}
          dataSource={data}
          columns={columns}
          pagination={false}
          scroll={{ x: 960 }}
          expandable={{
            expandedRowRender: (d) => <ParentsTable docId={d.id} />,
            rowExpandable: (d) => d.searchable,
          }}
          locale={{ emptyText: '还没有条款，上传 PDF 或在后端执行 python -m app.rag ingest --all' }}
        />
      )}
    </Flex>
  )
}
