import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { type ReactNode, useEffect, useState } from 'react'

import { USER_SCOPE } from '../lib/queryKeys'
import { useAuthStore } from '../stores/auth'

export function Providers({ children }: { children: ReactNode }) {
  // 每个 Providers 实例一个 QueryClient，测试之间互不影响
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } }),
  )

  // 登录身份变化时清掉个人数据缓存（约定见 lib/queryKeys.ts）
  useEffect(
    () =>
      useAuthStore.subscribe((state, prev) => {
        if (state.user?.id !== prev.user?.id || (prev.token && !state.token)) {
          queryClient.removeQueries({ queryKey: [USER_SCOPE] })
        }
      }),
    [queryClient],
  )

  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN}>
        <AntApp>{children}</AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
