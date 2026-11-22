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
  /** 最近一次操作的结果 */
  status: DocumentStatus
  /** 后台正在入库（服务进程内） */
  processing: boolean
  /** 库里有可检索的块（可能是旧版本） */
  searchable: boolean
  /** 库里的块来自旧版本文件，新版本尚未成功入库 */
  stale: boolean
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

export interface ParentPage {
  items: ParentPreview[]
  total: number
  offset: number
  limit: number
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type SearchMode = 'vector' | 'hybrid' | 'hybrid_rerank'

export const SEARCH_MODE_LABELS: Record<SearchMode, string> = {
  vector: '纯向量',
  hybrid: '混合（向量 + 全文）',
  hybrid_rerank: '混合 + 重排',
}

export interface SearchHit {
  parent_id: number
  child_id: number
  document_id: number
  doc_title: string
  section_path: string[]
  /** 命中的子块原文 */
  snippet: string
  /** 父块（章节）原文，作为回答上下文 */
  parent_content: string
  score: number
}

export interface SearchResponse {
  hits: SearchHit[]
  /** 各阶段耗时（毫秒）：embed_ms / recall_ms / rerank_ms / total_ms */
  timings: Record<string, number>
}

export interface LatencyStat {
  p50?: number
  p95?: number
}

export interface RetrievalMetrics {
  n: number
  'recall@1': number
  'recall@3': number
  'recall@5': number
  'recall@10': number
  'mrr@10': number
  latency_ms: {
    total: LatencyStat
    embed: LatencyStat
    recall: LatencyStat
    rerank: LatencyStat
    load?: LatencyStat
  }
}

export interface EvalRun {
  id: number
  suite: string
  dataset: string
  dataset_hash: string
  config: Record<string, unknown>
  metrics: Partial<RetrievalMetrics> & Record<string, unknown>
  created_at: string
}

// ---- 对话 ----

export interface ChatThread {
  id: string
  title: string
  created_at: string
  updated_at: string
}

/** 回答引用的条款章节；index 对应回答正文里的 [n] */
export interface Citation {
  index: number
  document_id: number
  doc_title: string
  section_path: string[]
  snippet: string
  parent_content: string
}

export interface ChatMessage {
  id: string | null
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
}

/** 对话接口的 SSE 事件 */
export type ChatEvent =
  | { type: 'thread'; id: string; title: string }
  | { type: 'status'; text: string }
  | { type: 'token'; text: string }
  | { type: 'citations'; items: Citation[] }
  | { type: 'done' }
  | { type: 'error'; message: string }
