import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuthStore } from '../stores/auth'

/** 未登录跳登录页，登录后回到原页面。后端同样会校验，这里只是体验。 */
export function RequireLogin({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const location = useLocation()
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return children
}
