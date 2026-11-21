import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Providers } from '../app/Providers'
import { routes } from '../app/routes'

export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const utils = render(
    <Providers>
      <RouterProvider router={router} />
    </Providers>,
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
