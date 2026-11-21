import { formatPremium, formatYuan } from './format'

test.each([
  ['561.00', '¥561'],
  ['285.30', '¥285.30'],
  ['2501.83', '¥2,501.83'],
  ['10000.00', '¥10,000'],
  ['0.50', '¥0.50'],
])('formatYuan(%s) = %s', (input, expected) => {
  expect(formatYuan(input)).toBe(expected)
})

test('有上限显示区间，无上限显示“起”', () => {
  expect(formatPremium({ min_premium: '285.30', max_premium: '2501.83' })).toBe(
    '¥285.30 - ¥2,501.83/年',
  )
  expect(formatPremium({ min_premium: '10000.00', max_premium: null })).toBe('¥10,000 起')
})
