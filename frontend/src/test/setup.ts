import '@testing-library/jest-dom/vitest'

import { useAuthStore } from '../stores/auth'

// 每个测试之间重置登录态与本地存储
afterEach(() => {
  useAuthStore.setState({ token: null, user: null })
  localStorage.clear()
})

// antd 的 Menu 等组件依赖 ResizeObserver，jsdom 没有实现
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// antd 依赖 matchMedia，jsdom 没有实现
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})
