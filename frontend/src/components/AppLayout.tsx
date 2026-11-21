import { Flex, Layout, Menu, Typography } from 'antd'
import { Link, Outlet, useLocation } from 'react-router'

import { BackendStatus } from './BackendStatus'
import { UserMenu } from './UserMenu'

const NAV_ITEMS = [
  { key: '/', label: <Link to="/">首页</Link> },
  { key: '/products', label: <Link to="/products">保险产品</Link> },
  // 后续里程碑加入：顾问对话、我的画像、管理后台
]

function selectedNav(pathname: string): string {
  if (pathname.startsWith('/products')) return '/products'
  return pathname
}

export function AppLayout() {
  const { pathname } = useLocation()

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
          items={NAV_ITEMS}
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
