import type { Product } from './types'

const yuanInteger = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  maximumFractionDigits: 0,
})
const yuanCents = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** 整数金额不带小数（¥561），有分的金额固定两位（¥285.30，而不是 ¥285.3）。 */
export function formatYuan(amount: string): string {
  const value = Number(amount)
  return Number.isInteger(value) ? yuanInteger.format(value) : yuanCents.format(value)
}

/** 保费区间展示："¥285.30 - ¥2,501.83/年"；只有下限时显示"¥10,000 起"。 */
export function formatPremium(p: Pick<Product, 'min_premium' | 'max_premium'>): string {
  const min = formatYuan(p.min_premium)
  if (p.max_premium === null) return `${min} 起`
  return `${min} - ${formatYuan(p.max_premium)}/年`
}
