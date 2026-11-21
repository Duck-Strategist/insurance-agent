import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Providers } from './Providers'
import { routes } from './routes'

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(
    <Providers>
      <RouterProvider router={router} />
    </Providers>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

test('首页渲染标题，后端正常时显示“服务正常”', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', database: 'ok' }), { status: 200 }),
    ),
  )
  renderAt('/')
  expect(screen.getByRole('heading', { name: '安心保 · 智能保险顾问' })).toBeInTheDocument()
  expect(await screen.findByText('服务正常')).toBeInTheDocument()
})

test('后端返回 503 时显示“服务不可用”', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'degraded' }), { status: 503 }),
    ),
  )
  renderAt('/')
  expect(await screen.findByText('服务不可用')).toBeInTheDocument()
})

test('未知路径显示 404', () => {
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
  renderAt('/no-such-page')
  expect(screen.getByText('页面不存在')).toBeInTheDocument()
})
