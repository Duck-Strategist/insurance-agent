import { fireEvent, screen, waitFor } from '@testing-library/react'

import type { ClauseDocument } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const admin = { id: 1, username: 'boss', role: 'admin' as const }

const doc = (over: Partial<ClauseDocument> = {}): ClauseDocument => ({
  id: 1,
  file_name: '众安尊享e生条款.pdf',
  status: 'indexed',
  processing: false,
  searchable: true,
  stale: false,
  error: null,
  parent_count: 120,
  child_count: 340,
  stats: {
    levels: { part: 1, article: 2, cn_paren: 3 },
    headings: { part: 7, article: 26, cn_paren: 44 },
    removed_lines: { repeated_header_footer: 66, page_number: 66 },
  },
  indexed_at: '2026-11-21T10:00:00Z',
  updated_at: '2026-11-21T10:00:00Z',
  products: [{ id: 6, name: '众安尊享e生百万医疗险2026版' }],
  ...over,
})

test('未登录访问管理页跳到登录页', async () => {
  mockFetch({ '/health': healthOk })
  const { router } = renderAt('/admin/documents')
  await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
})

test('普通用户看到 403，且导航里没有“条款管理”', async () => {
  useAuthStore.setState({ token: 't', user: { id: 2, username: 'u', role: 'user' } })
  mockFetch({ '/health': healthOk, '/api/v1/auth/me': () => jsonResponse({ id: 2, username: 'u', role: 'user' }) })
  renderAt('/admin/documents')
  expect(await screen.findByText('需要管理员权限')).toBeInTheDocument()
  expect(screen.queryByText('条款管理')).not.toBeInTheDocument()
})

test('管理员看到文档状态、父子块数和清洗统计', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () =>
      jsonResponse([doc(), doc({ id: 2, file_name: '失败.pdf', status: 'failed', error: 'boom', stats: {}, searchable: false })]),
  })
  renderAt('/admin/documents')
  expect(await screen.findByText('众安尊享e生条款.pdf')).toBeInTheDocument()
  expect(screen.getByText('120 / 340')).toBeInTheDocument()
  expect(screen.getByText('已入库')).toBeInTheDocument()
  expect(screen.getByText('失败')).toBeInTheDocument()
  expect(screen.getByText('页眉页脚 66，页码 66')).toBeInTheDocument()
  expect(screen.getByText('L1 第X部分×7 · L2 第X条×26 · L3 （一）×44')).toBeInTheDocument()
})

test('上传用 FormData 发送，不带 JSON Content-Type', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': (_url, init) =>
      init?.method === 'POST'
        ? jsonResponse(doc({ id: 3, file_name: '新.pdf', status: 'pending' }), 202)
        : jsonResponse([]),
  })
  const { container } = renderAt('/admin/documents')
  await screen.findByText('上传条款 PDF')

  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['%PDF-1.7'], '新.pdf', { type: 'application/pdf' })
  fireEvent.change(input, { target: { files: [file] } })

  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  )
  const [, init] = fetchMock.mock.calls.find(([, i]) => i?.method === 'POST')!
  expect(init!.body).toBeInstanceOf(FormData)
  expect((init!.body as FormData).get('file')).toBe(file)
  const headers = init!.headers as Headers
  expect(headers.has('Content-Type')).toBe(false)
  expect(headers.get('Authorization')).toBe('Bearer t')
})

test('入库被打断的文档显示“入库中断”，且不会触发轮询', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () =>
      jsonResponse([doc({ status: 'parsed', processing: false, searchable: false, stats: {} })]),
  })
  renderAt('/admin/documents')
  expect(await screen.findByText('入库中断')).toBeInTheDocument()
  expect(screen.getByText('不可检索')).toBeInTheDocument()
  const listCalls = () =>
    fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/api/v1/admin/documents')).length
  const before = listCalls()
  await new Promise((r) => setTimeout(r, 3500))
  expect(listCalls()).toBe(before)
}, 10_000)

test('重新入库失败但旧块还在时，提示检索使用旧版本', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents': () =>
      jsonResponse([doc({ status: 'failed', error: '限流', stale: true, stats: {} })]),
  })
  renderAt('/admin/documents')
  expect(await screen.findByText('失败')).toBeInTheDocument()
  expect(screen.getByText('检索使用旧版本')).toBeInTheDocument()
  // 旧块仍在：计数照常显示
  expect(screen.getByText('120 / 340')).toBeInTheDocument()
})

test('父块预览走服务端分页，能翻到 200 条以后', async () => {
  useAuthStore.setState({ token: 't', user: admin })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(admin),
    '/api/v1/admin/documents/1/parents': (url) => {
      const offset = Number(url.searchParams.get('offset'))
      const items = Array.from({ length: 20 }, (_, i) => ({
        id: offset + i + 1,
        seq: offset + i,
        section_path: [`第${offset + i}节`],
        chars: 10,
        child_count: 1,
        preview: 'x',
      }))
      return jsonResponse({ items, total: 293, offset, limit: 20 })
    },
    '/api/v1/admin/documents': () => jsonResponse([doc()]),
  })
  const { container } = renderAt('/admin/documents')
  await screen.findByText('众安尊享e生条款.pdf')
  fireEvent.click(container.querySelector('.ant-table-row-expand-icon')!)
  expect(await screen.findByText('共 293 个父块')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '页 15' }))
  expect(await screen.findByText('第280节')).toBeInTheDocument()
  const urls = fetchMock.mock.calls.map(([u]) => String(u))
  expect(urls.some((u) => u.includes('/parents?offset=280&limit=20'))).toBe(true)
})
