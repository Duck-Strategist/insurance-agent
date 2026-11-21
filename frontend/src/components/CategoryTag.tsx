import {
  HeartOutlined,
  MedicineBoxOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Tag } from 'antd'
import type { ReactNode } from 'react'

import { CATEGORY_LABELS, type ProductCategory } from '../lib/types'

const CATEGORY_STYLE: Record<ProductCategory, { color: string; icon: ReactNode }> = {
  medical: { color: 'blue', icon: <MedicineBoxOutlined /> },
  critical_illness: { color: 'magenta', icon: <HeartOutlined /> },
  accident: { color: 'orange', icon: <ThunderboltOutlined /> },
  life: { color: 'green', icon: <SafetyOutlined /> },
}

export function CategoryTag({ category }: { category: ProductCategory }) {
  const { color, icon } = CATEGORY_STYLE[category]
  return (
    <Tag color={color} icon={icon}>
      {CATEGORY_LABELS[category]}
    </Tag>
  )
}
