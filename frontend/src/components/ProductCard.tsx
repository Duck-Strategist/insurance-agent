import { Card, Flex, Space, Tag, Typography } from 'antd'
import { Link } from 'react-router'

import { formatPremium } from '../lib/format'
import type { Product } from '../lib/types'
import { CategoryTag } from './CategoryTag'

export function ProductCard({ product }: { product: Product }) {
  return (
    <Link to={`/products/${product.id}`} style={{ display: 'block', height: '100%' }}>
      <Card hoverable style={{ height: '100%' }}>
        <Flex vertical gap={8}>
          <Space size={4} wrap>
            <CategoryTag category={product.category} />
            <Typography.Text type="secondary">{product.insurer}</Typography.Text>
          </Space>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {product.name}
          </Typography.Title>
          <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0 }}>
            {product.description}
          </Typography.Paragraph>
          <Space size={[4, 4]} wrap>
            {product.highlights.map((h) => (
              <Tag key={h} bordered={false}>
                {h}
              </Tag>
            ))}
          </Space>
          <Typography.Text strong style={{ color: '#cf1322' }}>
            {formatPremium(product)}
          </Typography.Text>
        </Flex>
      </Card>
    </Link>
  )
}
