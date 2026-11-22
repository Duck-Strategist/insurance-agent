import type { PlanAction, PlanCardData, QuoteParams, QuoteParamSpec } from './types'

/** 表单值 → 报价参数：去掉未填项，金额去空格后按字符串提交。 */
export function toQuoteParams(
  specs: QuoteParamSpec[],
  values: Record<string, unknown>,
): QuoteParams {
  const params: QuoteParams = {}
  for (const spec of specs) {
    const v = values[spec.name]
    if (v === undefined || v === null || v === '') continue
    if (spec.kind === 'money') params[spec.name] = String(v).trim()
    else if (spec.kind === 'multi') params[spec.name] = Array.isArray(v) ? v.map(String) : []
    else if (spec.kind === 'age') params[spec.name] = Number(v)
    // 是/否单选用 'yes' | 'no' 取值（见 QuoteCalculator），提交给后端时转成布尔
    else if (spec.kind === 'bool') params[spec.name] = v === true || v === 'yes'
    else params[spec.name] = v as string
  }
  return params
}

const VALUE_LABELS: Record<string, string> = {
  male: '男',
  female: '女',
  single: '趸交',
  '3y': '3年交',
  '5y': '5年交',
  '6y': '6年交',
  '9y': '9年交',
  '10y': '10年交',
  '15y': '15年交',
  '20y': '20年交',
  '30y': '30年交',
  '35y': '35年交',
  life: '终身',
  to70: '保至70岁',
}

const MULTI_PAY_LABELS: Record<string, string> = {
  plan1: '多次给付方案一',
  plan2: '多次给付方案二',
}

/**
 * 已保存报价的参数摘要：「30 周岁 · 男 · 3年交 · 年交 10000.00 元」。
 * 可选责任不在这里展示：医疗险的可选责任在明细里每项一行，重疾险写在明细标题里。
 */
export function describeParams(params: QuoteParams): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) continue
    if (k === 'age') parts.push(`${v} 周岁`)
    else if (k === 'annual_premium') parts.push(`年交 ${v} 元`)
    else if (k === 'social_security') parts.push(v ? '有社保' : '无社保')
    else if (k === 'sum_assured') parts.push(`保额 ${v} 元`)
    else if (k === 'deductible') parts.push(`免赔额 ${v} 元`)
    else if (k === 'multi_pay') parts.push(MULTI_PAY_LABELS[String(v)] ?? String(v))
    else parts.push(VALUE_LABELS[String(v)] ?? String(v))
  }
  return parts.join(' · ')
}

/** 方案卡片操作写进对话的那句话，与后端 graph.edit_label 一致（历史消息去重依赖它）。 */
export function planActionLabel(plan: PlanCardData, action: PlanAction): string {
  if (action.action === 'confirm') return '确认方案'
  if (action.action === 'cancel') return '取消方案'
  const parts: string[] = []
  const swap = plan.items.filter((i) => action.remove_product_ids.includes(i.product_id))
  if (swap.length) parts.push('换一款：' + swap.map((i) => i.product_name).join('、'))
  const drop = plan.items.filter((i) => action.remove_categories.includes(i.category))
  if (drop.length) parts.push('不要：' + drop.map((i) => i.category_label).join('、'))
  return parts.join('；')
}
