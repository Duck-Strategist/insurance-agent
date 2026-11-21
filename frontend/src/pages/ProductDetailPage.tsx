import { ArrowLeftOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Descriptions, Flex, Result, Skeleton, Tag, Typography } from 'antd'
import { Link, useParams } from 'react-router'

import { CategoryTag } from '../components/CategoryTag'
import { ApiError, productApi } from '../lib/api'
import { formatPremium } from '../lib/format'

export function ProductDetailPage() {
  const { id } = useParams()
  const productId = Number(id)
  const validId = Number.isInteger(productId) && productId > 0

  const { data, isPending, isError, error } = useQuery({
    queryKey: ['product', productId],
    queryFn: () => productApi.get(productId),
    enabled: validId,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  })

  const back = (
    <Link to="/products">
      <Button icon={<ArrowLeftOutlined />}>返回产品列表</Button>
    </Link>
  )

  if (!validId || (isError && error instanceof ApiError && error.status === 404)) {
    return <Result status="404" title="产品不存在" extra={back} />
  }
  if (isError) {
    return <Alert type="error" showIcon message="产品加载失败" description={error.message} />
  }
  if (isPending) return <Skeleton active paragraph={{ rows: 8 }} />

  return (
    <Flex vertical gap={16}>
      <div>{back}</div>
      <Card>
        <Flex vertical gap={12}>
          <Flex gap={8} align="center" wrap>
            <CategoryTag category={data.category} />
            <Typography.Title level={3} style={{ margin: 0 }}>
              {data.name}
            </Typography.Title>
          </Flex>
          <Typography.Paragraph style={{ margin: 0 }}>{data.description}</Typography.Paragraph>
          <Descriptions column={{ xs: 1, md: 2 }} bordered size="middle">
            <Descriptions.Item label="保险公司">{data.insurer}</Descriptions.Item>
            <Descriptions.Item label="参考保费">{formatPremium(data)}</Descriptions.Item>
            <Descriptions.Item label="投保范围" span={2}>
              {data.target_group || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="产品亮点" span={2}>
              {data.highlights.map((h) => (
                <Tag key={h}>{h}</Tag>
              ))}
            </Descriptions.Item>
          </Descriptions>
          <Typography.Text type="secondary">
            保费为参考区间，实际金额以年龄、保额等因素计算为准。精确报价与条款问答将在 AI
            顾问中提供。
          </Typography.Text>
        </Flex>
      </Card>
    </Flex>
  )
}
