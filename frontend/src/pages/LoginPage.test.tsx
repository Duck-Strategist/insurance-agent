import { act, fireEvent, screen, waitFor } from '@testing-library/react'

import { useAuthStore } from '../stores/auth'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const tokenResponse = {
  access_token: 'jwt-token',
  token_type: 'bearer',
  expires_in: 86400,
  user: { id: 1, username: 'alice', role: 'user' },
}

function fillAndSubmit(username: string, password: string, submitText: string) {
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: username } })
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: password } })
  fireEvent.click(screen.getByRole('button', { name: submitText }))
}

test('登录成功后保存 token、跳回首页并显示用户名', async () => {
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/login': () => jsonResponse(tokenResponse),
    '/api/v1/auth/me': () => jsonResponse(tokenResponse.user),
  })
  const { router } = renderAt('/login')

  fillAndSubmit('alice', 'password123', '登 录')

  await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  expect(useAuthStore.getState().token).toBe('jwt-token')
  expect(await screen.findByRole('button', { name: /alice/ })).toBeInTheDocument()

  const loginCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/auth/login'))!
  expect(JSON.parse(String(loginCall[1]!.body))).toEqual({
    username: 'alice',
    password: 'password123',
  })
  // 登录后的请求自动带上 token
  const meCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/auth/me'))!
  expect((meCall[1]!.headers as Headers).get('Authorization')).toBe('Bearer jwt-token')
})

test('登录失败显示后端返回的错误信息', async () => {
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/login': () => jsonResponse({ detail: '用户名或密码错误' }, 401),
  })
  renderAt('/login')
  fillAndSubmit('alice', 'wrong-password', '登 录')
  expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument()
  expect(useAuthStore.getState().token).toBeNull()
})

test('注册时前端校验用户名格式，不发请求', async () => {
  const fetchMock = mockFetch({ '/health': healthOk })
  renderAt('/login')
  fireEvent.click(screen.getByRole('tab', { name: '注册' }))
  fillAndSubmit('a!', 'password123', '注册并登录')
  expect(await screen.findByText('3-32 位字母、数字或下划线')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/auth/register'))).toBe(false)
})

test('换号登录时不会沿用上一个用户的个人数据缓存', async () => {
  const users = [
    { id: 1, username: 'alice', role: 'user' },
    { id: 2, username: 'bob', role: 'user' },
  ]
  let current = 0
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(users[current]),
  })
  const meCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes('/auth/me')).length
  useAuthStore.setState({ token: 't-alice', user: users[0] as never })
  renderAt('/')
  expect(await screen.findByRole('button', { name: /alice/ })).toBeInTheDocument()

  // 模拟 alice 退出、bob 登录；若缓存未清，/me 的旧结果（alice）会覆盖 bob
  act(() => useAuthStore.getState().logout())
  current = 1
  act(() => useAuthStore.getState().setAuth('t-bob', users[1] as never))

  expect(await screen.findByRole('button', { name: /bob/ })).toBeInTheDocument()
  // 缓存被清掉才会为 bob 重新请求 /me；否则 5 分钟 staleTime 内直接用 alice 的缓存
  await waitFor(() => expect(meCalls()).toBe(2))
  expect(useAuthStore.getState().user?.username).toBe('bob')
})

test('本地 token 已失效时，/me 返回 401 会自动退出登录', async () => {
  useAuthStore.setState({ token: 'expired', user: tokenResponse.user as never })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse({ detail: '登录已失效，请重新登录' }, 401),
  })
  renderAt('/')
  await waitFor(() => expect(useAuthStore.getState().token).toBeNull())
  // 顶栏和首页各有一个登录入口
  expect(await screen.findAllByRole('button', { name: '登录 / 注册' })).toHaveLength(2)
})

test('本地 token 已失效时打开登录页，留在登录页而不是被弹回首页', async () => {
  // token 一天过期：第二天直接打开 /login，旧逻辑看到本地有 token 就跳走，/me 返回 401 后用户停在首页
  useAuthStore.setState({ token: 'expired', user: tokenResponse.user as never })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse({ detail: '登录已失效，请重新登录' }, 401),
  })
  const { router } = renderAt('/login')
  expect(await screen.findByLabelText('用户名')).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/login')
  expect(useAuthStore.getState().token).toBeNull()
})

test('本地 token 仍有效时打开登录页，校验通过后跳回来源页', async () => {
  useAuthStore.setState({ token: 'jwt-token', user: tokenResponse.user as never })
  mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(tokenResponse.user),
    '/api/v1/products': () => jsonResponse({ items: [], total: 0, page: 1, page_size: 12 }),
  })
  const { router } = renderAt('/login')
  await waitFor(() => expect(router.state.location.pathname).toBe('/'))
})
