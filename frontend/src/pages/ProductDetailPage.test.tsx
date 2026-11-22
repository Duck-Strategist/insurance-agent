import { fireEvent, screen, waitFor } from '@testing-library/react'

import { describeParams, toQuoteParams } from '../lib/quote'
import type { PricingInfo, Product, QuoteResult } from '../lib/types'
import { healthOk, jsonResponse, mockFetch, renderAt } from '../test/utils'

afterEach(() => vi.unstubAllGlobals())

const product: Product = {
  id: 7,
  name: '增额寿甲',
  category: 'life',
  insurer: '测试人寿',
  description: '描述',
  target_group: '0-70周岁',
  highlights: [],
  min_premium: '10000.00',
  max_premium: null,
}

const pricing: PricingInfo = {
  product_id: 7,
  supported: true,
  params: [
    { name: 'age', label: '被保险人年龄', kind: 'age', required: true, choices: [], min: 0, max: 70, hint: '' },
    {
      name: 'gender',
      label: '性别',
      kind: 'choice',
      required: true,
      choices: [
        { value: 'male', label: '男' },
        { value: 'female', label: '女' },
      ],
      min: null,
      max: null,
      hint: '',
    },
    { name: 'annual_premium', label: '年交保费（元）', kind: 'money', required: true, choices: [], min: null, max: null, hint: '' },
  ],
}

const ok: QuoteResult = {
  product_id: 7,
  product_name: '增额寿甲',
  status: 'ok',
  rate_version: 'abc123',
  params: { age: 30, gender: 'male', annual_premium: '10000.00' },
  missing: [],
  messages: [],
  lines: [
    { code: 'base', title: '基本保险金额表', premium: '10000.00', basis: '费率表第 1 页：30 周岁 男', page: 1 },
  ],
  annual_premium: '10000.00',
  pay_years: 3,
  total_premium: '30000.00',
  sum_assured: '28180.00',
}

test('按参数说明生成表单，提交后展示保额与计算依据', async () => {
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/products/7/pricing': () => jsonResponse(pricing),
    '/api/v1/products/7/quote': () => jsonResponse(ok),
    '/api/v1/products/7': () => jsonResponse(product),
  })
  renderAt('/products/7')

  fireEvent.change(await screen.findByRole('spinbutton'), { target: { value: '30' } })
  fireEvent.click(screen.getByRole('radio', { name: '男' }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: ' 10000 ' } })
  fireEvent.click(screen.getByRole('button', { name: '计算保费' }))

  expect(await screen.findByText('¥28,180')).toBeInTheDocument()
  expect(screen.getByText('费率表第 1 页：30 周岁 男')).toBeInTheDocument()
  // 未登录：提示登录后保存，不显示保存按钮
  expect(screen.queryByRole('button', { name: '保存报价' })).not.toBeInTheDocument()

  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/quote'))!
  expect(JSON.parse(String(call[1]?.body))).toEqual({
    params: { age: 30, gender: 'male', annual_premium: '10000' },
  })
})

test('超出投保范围的年龄原样提交，不被输入框改成上限', async () => {
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/products/7/pricing': () => jsonResponse(pricing),
    '/api/v1/products/7/quote': () =>
      jsonResponse({ ...ok, status: 'ineligible', messages: ['80 周岁不在投保年龄 0–70 周岁内'], lines: [] }),
    '/api/v1/products/7': () => jsonResponse(product),
  })
  renderAt('/products/7')
  const age = await screen.findByRole('spinbutton')
  fireEvent.change(age, { target: { value: '80' } })
  fireEvent.blur(age)
  fireEvent.click(screen.getByRole('radio', { name: '男' }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '10000' } })
  fireEvent.click(screen.getByRole('button', { name: '计算保费' }))
  expect(await screen.findByText('80 周岁不在投保年龄 0–70 周岁内')).toBeInTheDocument()
  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/quote'))!
  expect(JSON.parse(String(call[1]?.body)).params.age).toBe(80)
})

test('不支持报价的产品显示提示', async () => {
  mockFetch({
    '/health': healthOk,
    '/api/v1/products/7/pricing': () => jsonResponse({ product_id: 7, supported: false, params: [] }),
    '/api/v1/products/7': () => jsonResponse(product),
  })
  renderAt('/products/7')
  expect(await screen.findByText('该产品暂不支持在线报价。')).toBeInTheDocument()
})

test('不可投保时展示原因', async () => {
  mockFetch({
    '/health': healthOk,
    '/api/v1/products/7/pricing': () => jsonResponse(pricing),
    '/api/v1/products/7/quote': () =>
      jsonResponse({ ...ok, status: 'ineligible', messages: ['65 周岁不能选5年交，可选：趸交'], lines: [] }),
    '/api/v1/products/7': () => jsonResponse(product),
  })
  renderAt('/products/7')
  fireEvent.change(await screen.findByRole('spinbutton'), { target: { value: '65' } })
  fireEvent.click(screen.getByRole('radio', { name: '男' }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '10000' } })
  fireEvent.click(screen.getByRole('button', { name: '计算保费' }))
  await waitFor(() =>
    expect(screen.getByText('65 周岁不能选5年交，可选：趸交')).toBeInTheDocument(),
  )
})

test('toQuoteParams 去掉空值并规范类型', () => {
  const specs = [
    ...pricing.params,
    { name: 'addons', label: '可选责任', kind: 'multi' as const, required: false, choices: [], min: null, max: null, hint: '' },
  ]
  expect(toQuoteParams(specs, { age: '30', gender: undefined, annual_premium: ' 5000.5 ', addons: ['drug'] })).toEqual({
    age: 30,
    annual_premium: '5000.5',
    addons: ['drug'],
  })
  // 是/否单选的取值是 'yes' | 'no'，提交时转成布尔
  const ss = { name: 'social_security', label: '是否有社保', kind: 'bool' as const, required: true, choices: [], min: null, max: null, hint: '' }
  expect(toQuoteParams([ss], { social_security: 'no' })).toEqual({ social_security: false })
  expect(toQuoteParams([ss], { social_security: 'yes' })).toEqual({ social_security: true })
})

test('describeParams 生成可读摘要', () => {
  expect(describeParams({ age: 30, gender: 'male', pay_term: '3y', annual_premium: '10000.00' })).toBe(
    '30 周岁 · 男 · 3年交 · 年交 10000.00 元',
  )
  expect(describeParams({ age: 30, social_security: false, addons: ['drug'] })).toBe('30 周岁 · 无社保')
  expect(
    describeParams({
      age: 30,
      gender: 'female',
      pay_term: '30y',
      sum_assured: '300000.00',
      coverage: 'to70',
      multi_pay: 'plan1',
      options: ['death'],
    }),
  ).toBe('30 周岁 · 女 · 30年交 · 保额 300000.00 元 · 保至70岁 · 多次给付方案一')
})

test('非必填的单选有「不选」，选它时不提交该参数', async () => {
  const withOptional: PricingInfo = {
    ...pricing,
    params: [
      ...pricing.params,
      {
        name: 'multi_pay',
        label: '重大疾病多次给付',
        kind: 'choice',
        required: false,
        choices: [{ value: 'plan1', label: '方案一' }],
        min: null,
        max: null,
        hint: '',
      },
    ],
  }
  const fetchMock = mockFetch({
    '/health': healthOk,
    '/api/v1/products/7/pricing': () => jsonResponse(withOptional),
    '/api/v1/products/7/quote': () => jsonResponse(ok),
    '/api/v1/products/7': () => jsonResponse(product),
  })
  renderAt('/products/7')
  fireEvent.change(await screen.findByRole('spinbutton'), { target: { value: '30' } })
  fireEvent.click(screen.getByRole('radio', { name: '男' }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '10000' } })
  fireEvent.click(screen.getByRole('radio', { name: '方案一' }))
  fireEvent.click(screen.getByRole('radio', { name: '不选' }))
  fireEvent.click(screen.getByRole('button', { name: '计算保费' }))
  await screen.findByText('¥28,180')
  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/quote'))!
  expect(JSON.parse(String(call[1]?.body)).params).not.toHaveProperty('multi_pay')
})
