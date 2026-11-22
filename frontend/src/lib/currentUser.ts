import { useQuery } from '@tanstack/react-query'

import { useAuthStore } from '../stores/auth'
import { authApi } from './api'
import { queryKeys } from './queryKeys'

/**
 * 用 /me 校验本地 token。token 一天过期、用户也可能已被删除：失效时 request() 收到 401 会清掉登录态。
 * 顶栏和登录页共用同一个查询（同一个 queryKey，只发一次请求）。
 */
export function useCurrentUser() {
  const token = useAuthStore((s) => s.token)
  const setUser = useAuthStore((s) => s.setUser)
  return useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: async () => {
      const me = await authApi.me()
      setUser(me)
      return me
    },
    enabled: Boolean(token),
    retry: false,
    staleTime: 5 * 60_000,
  })
}
