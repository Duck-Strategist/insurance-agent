import {
  CalculatorOutlined,
  FileProtectOutlined,
  IdcardOutlined,
  LogoutOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Button, Dropdown } from 'antd'
import { useLocation, useNavigate } from 'react-router'

import { useCurrentUser } from '../lib/currentUser'
import { useAuthStore } from '../stores/auth'

/** 顶栏右侧：未登录显示登录按钮；已登录显示用户名和退出。 */
export function UserMenu() {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()
  const location = useLocation()

  // 页面加载时用 /me 校验本地 token：过期会 401，request() 会自动清掉登录态
  useCurrentUser()

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
        items: [
          { key: 'profile', icon: <IdcardOutlined />, label: '我的画像' },
          { key: 'plans', icon: <FileProtectOutlined />, label: '我的方案' },
          { key: 'quotes', icon: <CalculatorOutlined />, label: '我的报价' },
          { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
        ],
        onClick: ({ key }) => {
          if (key === 'profile') navigate('/profile')
          if (key === 'plans') navigate('/plans')
          if (key === 'quotes') navigate('/quotes')
          if (key === 'logout') {
            // 个人数据缓存由 Providers 在登录态变化时统一清理
            logout()
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
