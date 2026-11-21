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

export type DocumentStatus = 'pending' | 'parsed' | 'indexed' | 'failed'

export interface DocumentStats {
  input_chars?: number
  body_chars?: number
  removed_lines?: Record<string, number>
  headings?: Record<string, number>
  levels?: Record<string, number>
  sections?: number
  title?: string | null
  max_child_chars?: number
}

export interface ClauseDocument {
  id: number
  file_name: string
  status: DocumentStatus
  processing: boolean
  error: string | null
  parent_count: number
  child_count: number
  stats: DocumentStats
  indexed_at: string | null
  updated_at: string
  products: { id: number; name: string }[]
}

export interface ParentPreview {
  id: number
  seq: number
  section_path: string[]
  chars: number
  child_count: number
  preview: string
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
