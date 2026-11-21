import { Layout, Menu, Typography } from 'antd'
import { Link, Outlet, useLocation } from 'react-router'

import { BackendStatus } from './BackendStatus'

const NAV_ITEMS = [
  { key: '/', label: <Link to="/">首页</Link> },
  // M1 起逐步加入：产品、顾问对话、我的画像、管理后台
]

export function AppLayout() {
  const { pathname } = useLocation()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}>
          安心保
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[pathname]}
          items={NAV_ITEMS}
          style={{ flex: 1, minWidth: 0 }}
        />
        <span style={{ color: '#fff' }}>
          <BackendStatus />
        </span>
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Outlet />
      </Layout.Content>
    </Layout>
  )
}
