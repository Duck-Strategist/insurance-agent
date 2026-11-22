import { create } from 'zustand'

import type { Citation, PlanCardData } from '../lib/types'

/**
 * 正在进行中的一轮对话（流式状态，spec D22：客户端状态放 Zustand）。
 * 回答完成后从服务端重新拉取历史消息，这里清空；历史消息本身由 TanStack Query 管理。
 */
interface ChatStreamState {
  streaming: boolean
  threadId: string | null
  question: string
  answer: string
  status: string | null
  citations: Citation[]
  /** 本轮生成的方案卡片 */
  plan: PlanCardData | null
  error: string | null
  start: (threadId: string | null, question: string) => void
  setThread: (threadId: string) => void
  appendAnswer: (text: string) => void
  setStatus: (text: string) => void
  setCitations: (items: Citation[]) => void
  setPlan: (plan: PlanCardData) => void
  fail: (message: string) => void
  finish: () => void
  reset: () => void
}

const idle = {
  streaming: false,
  threadId: null,
  question: '',
  answer: '',
  status: null,
  citations: [],
  plan: null,
  error: null,
}

export const useChatStream = create<ChatStreamState>()((set) => ({
  ...idle,
  start: (threadId, question) => set({ ...idle, streaming: true, threadId, question }),
  setThread: (threadId) => set({ threadId }),
  appendAnswer: (text) => set((s) => ({ answer: s.answer + text, status: null })),
  setStatus: (text) => set({ status: text }),
  setCitations: (items) => set({ citations: items }),
  setPlan: (plan) => set({ plan: { ...plan, status: 'pending' } }),
  fail: (message) => set({ streaming: false, status: null, error: message }),
  finish: () => set({ streaming: false, status: null }),
  reset: () => set(idle),
}))
