import { fireEvent, screen, waitFor } from '@testing-library/react'

import type { Profile } from '../lib/types'
import { useAuthStore } from '../stores/auth'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const user = { id: 1, username: 'alice', role: 'user' as const }

const profile: Profile = {
  birth_date: '1996-05-24',
  gender: 'male',
  social_security: true,
  occupation_class: null,
  city: '杭州',
  annual_budget: '5000.00',
  family: [
    { id: 'w1', relation: 'spouse', name: '老婆', birth_date: '1998-05-24', birth_date_estimated: true,
      gender: 'female', social_security: null },
  ],
  existing_coverage: [
    { insured: 'w1', product_id: null, product_name: '公司团险', category: null, sum_assured: '100000.00',
      start_date: null },
  ],
  age: 30,
  field_sources: { birth_date: 'chat_age', city: 'chat', gender: 'manual' },
  updated_at: '2026-11-22T10:00:00Z',
}

test('我的画像：展示来源标记，保存时提交整份画像', async () => {
  useAuthStore.setState({ token: 't', user })
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/auth/me': () => jsonResponse(user),
    '/api/v1/products': () => jsonResponse({ items: [], total: 0, page: 1, page_size: 50 }),
    '/api/v1/profile': (_, init) =>
      init?.method === 'PUT' ? jsonResponse({ ...profile, city: '上海' }) : jsonResponse(profile),
  })
  renderAt('/profile')

  expect(await screen.findByText('按对话中的年龄估算')).toBeInTheDocument()
  expect(screen.getByText('来自对话')).toBeInTheDocument()
  expect(screen.getByText('（30 周岁）')).toBeInTheDocument()
  expect(screen.getByText('按对话中的年龄估算，可改成准确日期')).toBeInTheDocument()

  const city = screen.getByLabelText('城市')
  await waitFor(() => expect(city).toHaveValue('杭州'))
  fireEvent.change(city, { target: { value: '上海' } })
  fireEvent.click(screen.getByRole('button', { name: '保存画像' }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(call).toBeDefined()
    const body = JSON.parse(String(call?.[1]?.body))
    expect(body.city).toBe('上海')
    expect(body.family[0]).toMatchObject({ id: 'w1', birth_date_estimated: true })
    expect(body.existing_coverage[0]).toMatchObject({ insured: 'w1', product_name: '公司团险' })
    expect(body).not.toHaveProperty('field_sources')
  })
  expect(await screen.findByText('画像已保存')).toBeInTheDocument()
})
