import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Col, Empty, Flex, Pagination, Row, Segmented, Skeleton, Typography } from 'antd'
import { useSearchParams } from 'react-router'

import { ProductCard } from '../components/ProductCard'
import { productApi } from '../lib/api'
import { CATEGORY_LABELS, type ProductCategory } from '../lib/types'

const PAGE_SIZE = 12
const ALL = 'all'
const CATEGORY_OPTIONS = [
  { label: '全部', value: ALL },
  ...Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ label, value })),
]

function parseCategory(raw: string | null): ProductCategory | undefined {
  return raw && raw in CATEGORY_LABELS ? (raw as ProductCategory) : undefined
}

export function ProductsPage() {
  // 筛选与页码放在 URL 里：可分享、刷新不丢、浏览器后退可用
  const [params, setParams] = useSearchParams()
  const category = parseCategory(params.get('category'))
  const page = Math.max(1, Number(params.get('page')) || 1)

  const { data, isPending, isError, error } = useQuery({
    queryKey: ['products', category ?? ALL, page],
    queryFn: () => productApi.list({ category, page, pageSize: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })

  const update = (next: { category?: string; page?: number }) => {
    const p = new URLSearchParams(params)
    if (next.category !== undefined) {
      if (next.category === ALL) p.delete('category')
      else p.set('category', next.category)
      p.delete('page') // 换分类回到第一页
    }
    if (next.page !== undefined) {
      if (next.page <= 1) p.delete('page')
      else p.set('page', String(next.page))
    }
    setParams(p)
  }

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          保险产品
        </Typography.Title>
        <Segmented
          options={CATEGORY_OPTIONS}
          value={category ?? ALL}
          onChange={(v) => update({ category: String(v) })}
        />
      </Flex>

      {isError ? (
        <Alert type="error" showIcon message="产品加载失败" description={error.message} />
      ) : isPending ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : data.items.length === 0 ? (
        <Empty description="暂无产品" />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            {data.items.map((p) => (
              <Col key={p.id} xs={24} sm={12} lg={8} xl={6}>
                <ProductCard product={p} />
              </Col>
            ))}
          </Row>
          <Pagination
            align="center"
            current={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            hideOnSinglePage
            showSizeChanger={false}
            onChange={(p) => update({ page: p })}
          />
        </>
      )}
    </Flex>
  )
}
