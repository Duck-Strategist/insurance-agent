import { fireEvent, screen, waitFor } from '@testing-library/react'

import type { Page, Product } from '../lib/types'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const product = (id: number, name: string, category: Product['category']): Product => ({
  id,
  name,
  category,
  insurer: '测试保险',
  description: `${name}的描述`,
  target_group: '18-60周岁',
  highlights: ['亮点'],
  min_premium: '100.00',
  max_premium: id % 2 ? '500.00' : null,
})

const ALL: Product[] = [
  product(1, '医疗险甲', 'medical'),
  product(2, '寿险乙', 'life'),
  product(3, '寿险丙', 'life'),
]

function productsHandler(url: URL) {
  const category = url.searchParams.get('category')
  const items = category ? ALL.filter((p) => p.category === category) : ALL
  const page: Page<Product> = { items, total: items.length, page: 1, page_size: 12 }
  return jsonResponse(page)
}

test('产品列表渲染卡片，保费格式正确', async () => {
  mockFetch({ '/health': healthOk, '/api/v1/products': productsHandler })
  renderAt('/products')
  expect(await screen.findByText('医疗险甲')).toBeInTheDocument()
  expect(screen.getByText('寿险乙')).toBeInTheDocument()
  // id 为奇数的产品有保费上限
  expect(screen.getAllByText('¥100 - ¥500/年')).toHaveLength(2)
  expect(screen.getAllByText('¥100 起')).toHaveLength(1)
})

test('切换分类会按 category 请求并只显示该类产品', async () => {
  const fetchMock = mockFetch({ '/health': healthOk, '/api/v1/products': productsHandler })
  const { router } = renderAt('/products')
  await screen.findByText('医疗险甲')

  fireEvent.click(screen.getByRole('radio', { name: '寿险' }))

  await waitFor(() => expect(screen.queryByText('医疗险甲')).not.toBeInTheDocument())
  expect(screen.getByText('寿险乙')).toBeInTheDocument()
  expect(router.state.location.search).toBe('?category=life')
  const urls = fetchMock.mock.calls.map(([u]) => String(u))
  expect(urls.some((u) => u.includes('category=life'))).toBe(true)
})

test('页码越界时自动跳到最后一页', async () => {
  mockFetch({ '/health': healthOk, '/api/v1/products': productsHandler })
  const { router } = renderAt('/products?category=life&page=9')
  // 寿险只有 2 个，只有 1 页 → 去掉 page 参数，显示产品而不是"暂无产品"
  // 先请求越界页、再跳转重新请求：两次往返，并行跑全部测试时默认的 1 秒不够
  expect(await screen.findByText('寿险乙', undefined, { timeout: 4000 })).toBeInTheDocument()
  expect(router.state.location.search).toBe('?category=life')
  expect(screen.queryByText('暂无产品')).not.toBeInTheDocument()
})

test('小数页码按整数请求，不报错', async () => {
  const fetchMock = mockFetch({ '/health': healthOk, '/api/v1/products': productsHandler })
  renderAt('/products?page=1.7')
  expect(await screen.findByText('医疗险甲')).toBeInTheDocument()
  const urls = fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('products'))
  expect(urls.every((u) => u.includes('page=1&'))).toBe(true)
})

test('详情页 404 显示“产品不存在”', async () => {
  mockFetch({
    '/health': healthOk,
    '/api/v1/products/': () => jsonResponse({ detail: '产品不存在' }, 404),
  })
  renderAt('/products/999')
  expect(await screen.findByText('产品不存在')).toBeInTheDocument()
})
