/** 统一的请求封装：注入 JWT、把后端错误转成 ApiError、401 时清除登录态。 */

import { useAuthStore } from '../stores/auth'
import type {
  ClauseDocument,
  EvalRun,
  Page,
  ParentPage,
  Product,
  ProductCategory,
  SearchMode,
  SearchResponse,
  TokenResponse,
  User,
} from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 后端错误体是 {detail: string} 或 422 的 {detail: [{msg}]}，取出可读的信息。 */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { msg?: string }
      if (first.msg) return first.msg
    }
  } catch {
    // 非 JSON 响应，用状态码兜底
  }
  return `请求失败（${response.status}）`
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 用 Headers 合并：调用方可以传对象、数组或 Headers 实例，都不会丢
  const headers = new Headers(init?.headers)
  // 只给字符串（JSON）请求体加类型；FormData 由浏览器自动带上 multipart 边界，不能手动设置
  if (typeof init?.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = useAuthStore.getState().token
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    // 带着 token 却 401：token 过期或用户已不存在，清掉登录态让用户重新登录
    if (response.status === 401 && token) {
      useAuthStore.getState().logout()
    }
    throw new ApiError(response.status, await errorMessage(response))
  }
  // 204 / 空响应体没有 JSON 可解析
  if (response.status === 204 || response.headers.get('Content-Length') === '0') {
    return undefined as T
  }
  return (await response.json()) as T
}

const json = (body: unknown) => JSON.stringify(body)

// ---- 健康检查 ----
export interface HealthResponse {
  status: string
  database?: string | null
}

export const getReadiness = () => request<HealthResponse>('/health/ready')

// ---- 认证 ----
export interface Credentials {
  username: string
  password: string
}

export const authApi = {
  login: (c: Credentials) =>
    request<TokenResponse>('/api/v1/auth/login', { method: 'POST', body: json(c) }),
  register: (c: Credentials) =>
    request<TokenResponse>('/api/v1/auth/register', { method: 'POST', body: json(c) }),
  me: () => request<User>('/api/v1/auth/me'),
}

// ---- 产品 ----
export interface ProductQuery {
  category?: ProductCategory
  page?: number
  pageSize?: number
}

export const productApi = {
  list: ({ category, page = 1, pageSize = 12 }: ProductQuery = {}) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (category) params.set('category', category)
    return request<Page<Product>>(`/api/v1/products?${params}`)
  },
  get: (id: number) => request<Product>(`/api/v1/products/${id}`),
}

// ---- 管理后台 ----
export const adminApi = {
  listDocuments: () => request<ClauseDocument[]>('/api/v1/admin/documents'),
  reindex: (id: number) =>
    request<{ status: string }>(`/api/v1/admin/documents/${id}/reindex`, { method: 'POST' }),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ClauseDocument>('/api/v1/admin/documents', { method: 'POST', body: form })
  },
  parents: (id: number, offset: number, limit: number) =>
    request<ParentPage>(`/api/v1/admin/documents/${id}/parents?offset=${offset}&limit=${limit}`),
  search: (body: { query: string; mode: SearchMode; document_id?: number }) =>
    request<SearchResponse>('/api/v1/admin/search', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  evalRuns: (suite?: string) =>
    request<EvalRun[]>(`/api/v1/admin/eval-runs${suite ? `?suite=${encodeURIComponent(suite)}` : ''}`),
}
