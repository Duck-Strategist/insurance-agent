import { fireEvent, screen, waitFor } from '@testing-library/react'

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
