import { useQuery } from '@tanstack/react-query'
import { Alert, Empty, Flex, Skeleton, Typography } from 'antd'
import { Link } from 'react-router'

import { PlanCard } from '../components/PlanCard'
import { planApi } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'

/** 我的方案：顾问生成的、已确认和待确认的保障方案。 */
export function PlansPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: queryKeys.plans,
    queryFn: planApi.list,
  })

  if (isPending) return <Skeleton active paragraph={{ rows: 6 }} />
  if (isError) return <Alert type="error" showIcon message="方案加载失败" description={error.message} />

  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        我的方案
      </Typography.Title>
      {data.length === 0 ? (
        <Empty
          description={
            <span>
              还没有方案，可以在<Link to="/chat">智能顾问</Link>里告诉我年龄和预算来配置
            </span>
          }
        />
      ) : (
        data.map((p) => (
          <Flex key={p.id} vertical gap={4}>
            <Typography.Text type="secondary">
              {new Date(p.created_at).toLocaleString('zh-CN')}
              {p.thread_id && (
                <>
                  {' · '}
                  <Link to={`/chat/${p.thread_id}`}>查看对话</Link>
                </>
              )}
            </Typography.Text>
            {/* 这里只展示；待确认的方案要回到对话里确认（确认依赖对话的中断状态） */}
            <PlanCard
              plan={{
                plan_id: p.id,
                slots: p.slots,
                items: p.items,
                dropped: p.summary.dropped,
                budget: p.summary.budget,
                total_annual_premium: p.summary.total_annual_premium,
                status: p.status,
              }}
            />
          </Flex>
        ))
      )}
    </Flex>
  )
}
