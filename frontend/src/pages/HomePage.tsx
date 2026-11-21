import { Button, Card, Flex, Typography } from 'antd'
import { Link } from 'react-router'

import { useAuthStore } from '../stores/auth'

export function HomePage() {
  const user = useAuthStore((s) => s.user)

  return (
    <Card>
      <Typography.Title level={2}>安心保 · 智能保险顾问</Typography.Title>
      <Typography.Paragraph type="secondary">
        条款问答、方案推荐、精确报价、理赔咨询——AI 顾问建设中。
        {user && ` 欢迎回来，${user.username}。`}
      </Typography.Paragraph>
      <Flex gap={12}>
        <Link to="/products">
          <Button type="primary">浏览保险产品</Button>
        </Link>
        {!user && (
          <Link to="/login">
            <Button>登录 / 注册</Button>
          </Link>
        )}
      </Flex>
    </Card>
  )
}
