import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

import { useAuthStore } from '../stores/auth'

// findBy* / waitFor 默认只等 1 秒。全量并行时 antd 表单页首屏渲染加上按角色查询会超过 1 秒
// （ProductDetailPage 曾在 findByRole('spinbutton') 处偶发失败），只调 vite.config 的 testTimeout 管不到这里。
// 真正找不到元素的测试仍会失败，只是多等几秒。
configure({ asyncUtilTimeout: 5_000 })

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
