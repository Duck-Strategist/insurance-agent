import { Result } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuthStore } from '../stores/auth'

/** 未登录跳登录页（登录后回来）；已登录但不是管理员显示 403。后端同样会校验，这里只是体验。 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const location = useLocation()

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  if (user.role !== 'admin') {
    return <Result status="403" title="需要管理员权限" />
  }
  return children
}
