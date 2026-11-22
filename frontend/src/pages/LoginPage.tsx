import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Card, Flex, Form, Input, Spin, Tabs, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'

import { authApi, type Credentials } from '../lib/api'
import { useCurrentUser } from '../lib/currentUser'
import { safeRedirect } from '../lib/navigation'
import { useAuthStore } from '../stores/auth'

type Mode = 'login' | 'register'

export function LoginPage() {
  const [mode, setMode] = useState<Mode>('login')
  const navigate = useNavigate()
  const location = useLocation()
  const token = useAuthStore((s) => s.token)
  const setAuth = useAuthStore((s) => s.setAuth)
  const redirectTo = safeRedirect((location.state as { from?: unknown } | null)?.from)

  const mutation = useMutation({
    mutationFn: (c: Credentials) => (mode === 'login' ? authApi.login(c) : authApi.register(c)),
    onSuccess: (res) => {
      setAuth(res.access_token, res.user)
      navigate(redirectTo, { replace: true })
    },
  })

  // 本地有 token 不等于已登录（一天过期，用户也可能已被删除）：先用 /me 校验，通过才跳走。
  // 401 时 request() 清掉 token，这里就显示表单；网络错误等其他失败也显示表单，让用户重新登录
  const me = useCurrentUser()
  if (token && me.isSuccess && !mutation.isPending) return <Navigate to={redirectTo} replace />
  if (token && !me.isError && !mutation.isPending) {
    return (
      <Flex justify="center" style={{ paddingTop: 96 }}>
        <Spin />
      </Flex>
    )
  }

  return (
    <Flex justify="center" style={{ paddingTop: 48 }}>
      <Card style={{ width: 380, maxWidth: '100%' }}>
        <Typography.Title level={4} style={{ textAlign: 'center' }}>
          欢迎来到安心保
        </Typography.Title>
        <Tabs
          centered
          activeKey={mode}
          onChange={(k) => {
            setMode(k as Mode)
            mutation.reset()
          }}
          items={[
            { key: 'login', label: '登录' },
            { key: 'register', label: '注册' },
          ]}
        />
        {mutation.isError && (
          <Alert type="error" showIcon message={mutation.error.message} style={{ marginBottom: 16 }} />
        )}
        <Form<Credentials>
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => mutation.mutate(values)}
        >
          <Form.Item
            label="用户名"
            name="username"
            rules={
              mode === 'register'
                ? [
                    { required: true, message: '请输入用户名' },
                    { pattern: /^[a-zA-Z0-9_]{3,32}$/, message: '3-32 位字母、数字或下划线' },
                  ]
                : [{ required: true, message: '请输入用户名' }]
            }
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={
              mode === 'register'
                ? [
                    { required: true, message: '请输入密码' },
                    { min: 8, max: 128, message: '密码长度 8-128 位' },
                  ]
                : [{ required: true, message: '请输入密码' }]
            }
          >
            <Input.Password autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={mutation.isPending}>
            {mode === 'login' ? '登录' : '注册并登录'}
          </Button>
        </Form>
      </Card>
    </Flex>
  )
}
