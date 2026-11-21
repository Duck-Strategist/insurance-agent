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
      jsonResponse([doc(), doc({ id: 2, file_name: '失败.pdf', status: 'failed', error: 'boom', stats: {} })]),
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
