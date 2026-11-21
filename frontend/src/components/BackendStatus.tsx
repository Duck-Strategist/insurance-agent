import { useQuery } from '@tanstack/react-query'
import { Badge, Space } from 'antd'

import { getReadiness } from '../lib/api'

type Status = 'processing' | 'error' | 'success'

/** 顶栏右侧的后端连接状态，M0 用来验证前后端联通。文字颜色继承父元素，适配深色顶栏。 */
export function BackendStatus() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: getReadiness,
    refetchInterval: 30_000,
    retry: false,
  })

  let status: Status = 'success'
  let text = '服务正常'
  if (isPending) {
    status = 'processing'
    text = '连接中'
  } else if (isError || data.status !== 'ok') {
    status = 'error'
    text = '服务不可用'
  }

  return (
    <Space size={6}>
      <Badge status={status} />
      <span>{text}</span>
    </Space>
  )
}
