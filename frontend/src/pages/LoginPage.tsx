import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Card, Flex, Form, Input, Tabs, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'

import { authApi, type Credentials } from '../lib/api'
import { useAuthStore } from '../stores/auth'

type Mode = 'login' | 'register'

/** 只允许站内相对路径，防止 ?from=https://evil.com 这类开放重定向 */
function safeRedirect(from: unknown): string {
  return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/'
}

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

  if (token) return <Navigate to={redirectTo} replace />

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
