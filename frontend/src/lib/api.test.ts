import { ApiError, request } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

// Response 的 body 只能读一次，所以每次调用都克隆一个新的
function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockImplementation(async () => response.clone())
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('传入 Headers 实例时请求头不会丢失', async () => {
  const fetchMock = stubFetch(new Response('{}', { status: 200 }))
  await request('/api/x', { headers: new Headers({ Authorization: 'Bearer t' }) })
  const headers = fetchMock.mock.calls[0][1].headers as Headers
  expect(headers.get('Authorization')).toBe('Bearer t')
})

test('有请求体时默认加 JSON Content-Type，GET 不加', async () => {
  const fetchMock = stubFetch(new Response('{}', { status: 200 }))
  await request('/api/x', { method: 'POST', body: '{}' })
  await request('/api/x')
  expect((fetchMock.mock.calls[0][1].headers as Headers).get('Content-Type')).toBe(
    'application/json',
  )
  expect((fetchMock.mock.calls[1][1].headers as Headers).has('Content-Type')).toBe(false)
})

test('204 响应返回 undefined 而不是抛错', async () => {
  stubFetch(new Response(null, { status: 204 }))
  await expect(request('/api/x', { method: 'DELETE' })).resolves.toBeUndefined()
})

test('非 2xx 抛出带状态码的 ApiError', async () => {
  stubFetch(new Response('{}', { status: 404, statusText: 'Not Found' }))
  await expect(request('/api/x')).rejects.toMatchObject({ name: 'ApiError', status: 404 })
  expect(ApiError).toBeDefined()
})
