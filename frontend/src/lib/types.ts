export type ProductCategory = 'medical' | 'critical_illness' | 'accident' | 'life'

export const CATEGORY_LABELS: Record<ProductCategory, string> = {
  medical: '医疗险',
  critical_illness: '重疾险',
  accident: '意外险',
  life: '寿险',
}

export interface User {
  id: number
  username: string
  role: 'user' | 'admin'
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface Product {
  id: number
  name: string
  category: ProductCategory
  insurer: string
  description: string
  target_group: string
  highlights: string[]
  /** 两位小数的字符串，例如 "285.30" */
  min_premium: string
  max_premium: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
