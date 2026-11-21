import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
}

function isBusy(d: ClauseDocument) {
  return d.processing || d.status === 'pending' || d.status === 'parsed'
}

function ParentsTable({ docId }: { docId: number }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.adminParents(docId),
    queryFn: () => adminApi.parents(docId),
  })
  if (isError) return <Alert type="error" message={error.message} />
  return (
    <Table<ParentPreview>
      size="small"
      rowKey="id"
      loading={isPending}
      dataSource={data}
      pagination={{ pageSize: 20, hideOnSinglePage: true }}
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
    // 有文档在入库时每 3 秒刷新一次状态
    refetchInterval: (q) => (q.state.data?.some(isBusy) ? 3000 : false),
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
      width: 110,
      render: (s: DocumentStatus, d) =>
        d.processing ? (
          <Tag color="processing">入库中…</Tag>
        ) : (
          <Tooltip title={d.error}>
            <Tag color={STATUS[s].color}>{STATUS[s].label}</Tag>
          </Tooltip>
        ),
    },
    {
      title: '父块 / 子块',
      width: 110,
      render: (_, d) => (d.status === 'indexed' ? `${d.parent_count} / ${d.child_count}` : '—'),
    },
    {
      title: '标题识别',
      width: 200,
      render: (_, d) => {
        const levels = d.stats.levels
        if (!levels) return '—'
        const order = Object.entries(levels).sort((a, b) => a[1] - b[1])
        return (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {order
              .map(([k, lv]) => `L${lv} ${HEADING_LABELS[k] ?? k}×${d.stats.headings?.[k] ?? 0}`)
              .join(' · ')}
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
            rowExpandable: (d) => d.status === 'indexed',
          }}
          locale={{ emptyText: '还没有条款，上传 PDF 或在后端执行 python -m app.rag ingest --all' }}
        />
      )}
    </Flex>
  )
}
