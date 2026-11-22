import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Empty, Flex, List, Skeleton, Typography } from 'antd'
import { Link } from 'react-router'

import { pricingApi } from '../lib/api'
import { formatYuan } from '../lib/format'
import { queryKeys } from '../lib/queryKeys'
import { describeParams } from '../lib/quote'

/** 我的报价记录：每条带当时的参数、明细和费率版本，可复现。 */
export function QuotesPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.quotes,
    queryFn: pricingApi.list,
  })

  if (isPending) return <Skeleton active paragraph={{ rows: 6 }} />
  if (isError) return <Alert type="error" showIcon message="报价记录加载失败" description={error.message} />

  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        我的报价
      </Typography.Title>
      {data.length === 0 ? (
        <Empty description={<span>还没有保存的报价，可以在产品详情页试算后保存</span>} />
      ) : (
        data.map((q) => (
          <Card
            key={q.id}
            size="small"
            title={<Link to={`/products/${q.product_id}`}>{q.product_name}</Link>}
            extra={new Date(q.created_at).toLocaleString('zh-CN')}
          >
            <Flex vertical gap={8}>
              <Typography.Text>{describeParams(q.params)}</Typography.Text>
              <Typography.Text strong>
                年交 {formatYuan(q.annual_premium)} × {q.pay_years} 年 = {formatYuan(q.total_premium)}
                {q.sum_assured && `，基本保额 ${formatYuan(q.sum_assured)}`}
              </Typography.Text>
              <List
                size="small"
                dataSource={q.lines}
                renderItem={(line) => (
                  <List.Item extra={formatYuan(line.premium)}>
                    <List.Item.Meta title={line.title} description={line.basis} />
                  </List.Item>
                )}
              />
              <Typography.Text type="secondary">费率版本 {q.rate_version}</Typography.Text>
            </Flex>
          </Card>
        ))
      )}
    </Flex>
  )
}
