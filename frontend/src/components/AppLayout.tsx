import { Flex, Layout, Menu, Typography } from 'antd'
import { Link, Outlet, useLocation } from 'react-router'

import { useAuthStore } from '../stores/auth'
import { BackendStatus } from './BackendStatus'
import { UserMenu } from './UserMenu'

const NAV_ITEMS = [
  { key: '/', label: <Link to="/">首页</Link> },
  { key: '/products', label: <Link to="/products">保险产品</Link> },
  { key: '/chat', label: <Link to="/chat">智能顾问</Link> },
  // 后续里程碑加入：我的画像
]
const ADMIN_ITEMS = [
  { key: '/admin/documents', label: <Link to="/admin/documents">条款管理</Link> },
  { key: '/admin/search', label: <Link to="/admin/search">检索调试</Link> },
  { key: '/admin/evals', label: <Link to="/admin/evals">评测记录</Link> },
]

function selectedNav(pathname: string): string {
  if (pathname.startsWith('/products')) return '/products'
  if (pathname.startsWith('/chat')) return '/chat'
  return pathname
}

export function AppLayout() {
  const { pathname } = useLocation()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <Link to="/">
          <Typography.Title level={4} style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}>
            安心保
          </Typography.Title>
        </Link>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selectedNav(pathname)]}
          items={isAdmin ? [...NAV_ITEMS, ...ADMIN_ITEMS] : NAV_ITEMS}
          style={{ flex: 1, minWidth: 0 }}
        />
        <Flex align="center" gap={16} style={{ color: '#fff' }}>
          <BackendStatus />
          <UserMenu />
        </Flex>
      </Layout.Header>
      <Layout.Content style={{ padding: 24, maxWidth: 1280, width: '100%', margin: '0 auto' }}>
        <Outlet />
      </Layout.Content>
    </Layout>
  )
}
