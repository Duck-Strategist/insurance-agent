import { createCache, StyleProvider } from '@ant-design/cssinjs'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Providers } from '../app/Providers'
import { routes } from '../app/routes'

/**
 * 渲染整个应用到指定路径。
 *
 * antd 的 CSS-in-JS 默认把样式插进 DOM（一个页面约 70 个 style、2000 条规则），jsdom 的
 * getComputedStyle 每次都要遍历全部规则（约 8 ms），按角色查询会对每个元素调用它：
 * 一次 getByRole 要 1 秒，全量并行时测试偶发超时。测试不检查样式，所以按服务端模式
 * 只收集不插入（mock="server"），每次渲染用新的缓存。
 */
export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const utils = render(
    <StyleProvider mock="server" cache={createCache()}>
      <Providers>
        <RouterProvider router={router} />
      </Providers>
    </StyleProvider>,
  )
  return { ...utils, router }
}

type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>

/** 按路径前缀分发的 fetch 假实现；未匹配的请求返回 404。返回 mock 以便断言调用。 */
export function mockFetch(handlers: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const key = Object.keys(handlers)
      .sort((a, b) => b.length - a.length)
      .find((prefix) => url.pathname.startsWith(prefix))
    return key ? handlers[key](url, init) : new Response('{"detail":"not found"}', { status: 404 })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

export const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

export const healthOk = () => jsonResponse({ status: 'ok', database: 'ok' })
