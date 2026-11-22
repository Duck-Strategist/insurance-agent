/** 评测记录页的格式化与判断（不放在页面文件里：react-refresh lint） */

export const pct = (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—')
export const num = (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—')

export const INTENT_LABELS: Record<string, string> = {
  clause_qa: '条款问答',
  advisor: '顾问',
  claim: '理赔',
  chitchat: '寒暄',
  other: '其他',
}

/** 端到端评测的逐题结果（eval_runs.details 的一项） */
export interface E2EDetail {
  id: string
  question: string
  answerable: boolean
  answer: string
  cited_sections: { index: number; path: string[] }[]
  judge: {
    refused: boolean
    correctness: 'correct' | 'partial' | 'incorrect'
    citations: Record<string, boolean>
    reason: string
    parsed: boolean
  }
  no_hits: boolean
  latency_ms: number
}

/** 失败：没答全对、有答案题却拒答、无答案题没拒答、任一引用没有依据 */
export function E2E_FAILURE(d: E2EDetail): boolean {
  const badCite = Object.values(d.judge.citations).some((ok) => !ok)
  const refusedWrongly = d.answerable ? d.judge.refused : !d.judge.refused
  return d.judge.correctness !== 'correct' || refusedWrongly || badCite
}

const HUMAN_LABELS: Record<string, string> = {
  human: '抽检',
  human_test: '测试集',
  human_dev: '开发集',
}

/**
 * 运行记录里和人工标注比过的结果：`agreement --key` 写入 metrics 的 human、human_test、human_dev 等。
 * 开发集是调裁判时看过的标注，一致率偏高，排在最后并标明。
 */
export function humanAgreements(
  metrics: Record<string, unknown>,
): { key: string; label: string; data: Record<string, unknown> }[] {
  const order = ['human', 'human_test', 'human_dev']
  return Object.keys(metrics)
    .filter((k) => k.startsWith('human') && typeof metrics[k] === 'object' && metrics[k] !== null)
    .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
    .map((k) => ({ key: k, label: HUMAN_LABELS[k] ?? k, data: metrics[k] as Record<string, unknown> }))
}
