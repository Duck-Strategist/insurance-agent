/** 统一的请求封装：后续 M1 在这里注入 JWT、处理 401。 */

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 用 Headers 合并：调用方可以传对象、数组或 Headers 实例，都不会丢
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    throw new ApiError(response.status, `${response.status} ${response.statusText}`)
  }
  // 204 / 空响应体没有 JSON 可解析
  if (response.status === 204 || response.headers.get('Content-Length') === '0') {
    return undefined as T
  }
  return (await response.json()) as T
}

export interface HealthResponse {
  status: string
  database?: string | null
}

export const getReadiness = () => request<HealthResponse>('/health/ready')
