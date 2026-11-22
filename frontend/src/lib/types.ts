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
  /** 为 true 时各「第X部分」内部分别分配层级，levels 只是全文首次出现顺序 */
  levels_per_part?: boolean
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
  /** 顾问生成的方案卡片（含当前状态） */
  plan?: PlanCardData | null
}

// ---- 顾问方案（M6）----
export type PlanStatus = 'pending' | 'confirmed' | 'cancelled' | 'superseded' | 'expired' | 'unknown'

export interface PlanItem {
  product_id: number
  product_name: string
  category: ProductCategory
  category_label: string
  params: QuoteParams
  annual_premium: string
  pay_years: number
  total_premium: string
  sum_assured: string | null
  rate_version: string
  lines: { title: string; premium: string; basis: string; page: number }[]
}

export interface PlanCardData {
  plan_id: number
  slots: Record<string, unknown>
  items: PlanItem[]
  dropped: { category: ProductCategory; category_label: string; reason: string }[]
  budget: string
  total_annual_premium: string
  /** 历史消息里由服务端填写；流式过程中的新卡片视为 pending */
  status?: PlanStatus
}

export interface SavedPlan {
  id: number
  thread_id: string | null
  status: PlanStatus
  slots: Record<string, unknown>
  items: PlanItem[]
  summary: { budget: string; total_annual_premium: string; dropped: PlanCardData['dropped'] }
  created_at: string
  updated_at: string
}

export type PlanAction =
  | { action: 'confirm' }
  | { action: 'cancel' }
  /** 换一款：排除这些产品（同险种换成下一款）；remove_categories：这些险种不要了 */
  | { action: 'edit'; remove_product_ids: number[]; remove_categories: ProductCategory[] }

/** 对话接口的 SSE 事件 */
export type ChatEvent =
  | { type: 'thread'; id: string; title: string }
  | { type: 'status'; text: string }
  | { type: 'token'; text: string }
  | { type: 'citations'; items: Citation[] }
  | { type: 'plan_card'; plan: PlanCardData }
  | { type: 'plan_status'; plan_id: number; status: PlanStatus }
  | { type: 'done' }
  | { type: 'error'; message: string }

// ---- 报价（M5）----
export type ParamKind = 'age' | 'choice' | 'bool' | 'money' | 'multi'

export interface QuoteParamSpec {
  name: string
  label: string
  kind: ParamKind
  required: boolean
  choices: { value: string; label: string }[]
  min: number | null
  max: number | null
  hint: string
}

export interface PricingInfo {
  product_id: number
  /** false：暂不支持在线报价 */
  supported: boolean
  params: QuoteParamSpec[]
}

export type QuoteParams = Record<string, string | number | boolean | string[]>

export interface QuoteLine {
  code: string
  title: string
  premium: string
  /** 计算依据，如「费率表第 1 页：26–30 周岁 有社保 318.00 元」 */
  basis: string
  page: number
}

export interface QuoteResult {
  product_id: number
  product_name: string
  status: 'ok' | 'missing_params' | 'invalid' | 'ineligible'
  rate_version: string
  params: QuoteParams
  missing: string[]
  messages: string[]
  lines: QuoteLine[]
  annual_premium: string | null
  pay_years: number | null
  total_premium: string | null
  /** 终身寿险：由保费算出的基本保额 */
  sum_assured: string | null
}

export interface SavedQuote {
  id: number
  product_id: number
  product_name: string
  rate_version: string
  params: QuoteParams
  lines: QuoteLine[]
  annual_premium: string
  pay_years: number
  total_premium: string
  sum_assured: string | null
  created_at: string
}
