import { create } from 'zustand'

import type { Citation, PlanCardData, ProfileSuggestion } from '../lib/types'

/**
 * 正在进行中的一轮对话（流式状态，spec D22：客户端状态放 Zustand）。
 * 回答完成后从服务端重新拉取历史消息，这里清空；历史消息本身由 TanStack Query 管理。
 *
 * 画像建议（profile_suggestion）不随一轮结束清空：用户可能看完回答再决定保存或忽略。
 * 它只存在前端，刷新页面就没了（spec D20：只是建议，信息还在对话里，下次提到会再建议）。
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
  /** 会话 id → 待用户确认的画像建议 */
  suggestions: Record<string, ProfileSuggestion>
  start: (threadId: string | null, question: string) => void
  setThread: (threadId: string) => void
  appendAnswer: (text: string) => void
  setStatus: (text: string) => void
  setCitations: (items: Citation[]) => void
  setPlan: (plan: PlanCardData) => void
  setSuggestion: (threadId: string, suggestion: ProfileSuggestion) => void
  dismissSuggestion: (threadId: string) => void
  fail: (message: string) => void
  finish: () => void
  /** 一轮结束：清空流式内容，保留画像建议 */
  reset: () => void
  /** 退出登录、换账号：连画像建议一起清空，下一个登录的用户看不到 */
  clearAll: () => void
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
  suggestions: {},
  start: (threadId, question) => set({ ...idle, streaming: true, threadId, question }),
  setThread: (threadId) => set({ threadId }),
  appendAnswer: (text) => set((s) => ({ answer: s.answer + text, status: null })),
  setStatus: (text) => set({ status: text }),
  setCitations: (items) => set({ citations: items }),
  setPlan: (plan) => set({ plan: { ...plan, status: 'pending' } }),
  setSuggestion: (threadId, suggestion) =>
    set((s) => ({ suggestions: { ...s.suggestions, [threadId]: suggestion } })),
  dismissSuggestion: (threadId) =>
    set((s) => {
      const rest = { ...s.suggestions }
      delete rest[threadId]
      return { suggestions: rest }
    }),
  fail: (message) => set({ streaming: false, status: null, error: message }),
  finish: () => set({ streaming: false, status: null }),
  reset: () => set(idle),
  clearAll: () => set({ ...idle, suggestions: {} }),
}))
