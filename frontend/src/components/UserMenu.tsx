import { LogoutOutlined, UserOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dropdown } from 'antd'
import { useLocation, useNavigate } from 'react-router'

import { authApi } from '../lib/api'
import { useAuthStore } from '../stores/auth'

/** 顶栏右侧：未登录显示登录按钮；已登录显示用户名和退出。 */
export function UserMenu() {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()

  // 页面加载时用 /me 校验本地 token：过期会 401，request() 会自动清掉登录态
  useQuery({
    queryKey: ['me', token],
    queryFn: async () => {
      const me = await authApi.me()
      setUser(me)
      return me
    },
    enabled: Boolean(token),
    retry: false,
    staleTime: 5 * 60_000,
  })

  if (!token || !user) {
    return (
      <Button
        type="primary"
        // 登录后回到当前页面
        onClick={() => navigate('/login', { state: { from: location.pathname + location.search } })}
      >
        登录 / 注册
      </Button>
    )
  }

  return (
    <Dropdown
      menu={{
        items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
        onClick: ({ key }) => {
          if (key === 'logout') {
            logout()
            queryClient.removeQueries({ queryKey: ['me'] })
            navigate('/')
          }
        },
      }}
    >
      <Button type="text" icon={<UserOutlined />} style={{ color: '#fff' }}>
        {user.username}
      </Button>
    </Dropdown>
  )
}
