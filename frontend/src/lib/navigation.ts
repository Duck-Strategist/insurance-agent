/** URL 里的页码可能被手改成小数、负数或非数字，统一规整成 >=1 的整数。 */
export function parsePage(raw: string | null): number {
  const n = Math.floor(Number(raw))
  return Number.isFinite(n) && n >= 1 ? n : 1
}

/**
 * 只允许跳回本站页面，防止开放重定向。
 * 不能只看前缀：浏览器会把 "/\evil.com"、"/%5Cevil.com" 等当成协议相对地址，
 * 所以按浏览器规则解析成完整 URL，再比较 origin。
 */
export function safeRedirect(from: unknown): string {
  if (typeof from !== 'string' || !from.startsWith('/')) return '/'
  try {
    const url = new URL(from, window.location.origin)
    if (url.origin !== window.location.origin) return '/'
    return url.pathname + url.search + url.hash
  } catch {
    return '/'
  }
}
