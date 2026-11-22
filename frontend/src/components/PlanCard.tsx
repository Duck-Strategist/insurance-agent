import { Alert, Button, Card, Flex, List, Popconfirm, Radio, Tag, Typography } from 'antd'
import { useState } from 'react'

import { formatYuan } from '../lib/format'
import type { PlanAction, PlanCardData, PlanItem, PlanStatus } from '../lib/types'

const STATUS: Record<PlanStatus, { color: string; text: string }> = {
  pending: { color: 'processing', text: '待确认' },
  confirmed: { color: 'success', text: '已确认' },
  cancelled: { color: 'default', text: '已取消' },
  superseded: { color: 'default', text: '已调整' },
  expired: { color: 'default', text: '已失效' },
  unknown: { color: 'default', text: '未知' },
}

function itemDetail(item: PlanItem): string {
  const parts = [`年交 ${formatYuan(item.annual_premium)}`]
  if (item.pay_years > 1) parts.push(`交 ${item.pay_years} 年`)
  if (item.sum_assured) parts.push(`保额 ${formatYuan(item.sum_assured)}`)
  return parts.join(' · ')
}

/**
 * 顾问方案卡片。金额全部来自报价引擎（后端算好），这里只做展示。
 * 只有待确认（pending）且没有在生成中的卡片能操作；旧卡片上的按钮后端也会拒绝（409）。
 */
export function PlanCard({
  plan,
  busy = false,
  onAction,
}: {
  plan: PlanCardData
  /** 正在生成回答：按钮暂不可用 */
  busy?: boolean
  onAction?: (action: PlanAction) => void
}) {
  // 每项的调整：保留 / 换一款（同险种换下一款产品）/ 不要这个险种
  const [changes, setChanges] = useState<Record<number, 'swap' | 'drop'>>({})
  const swap = plan.items.filter((i) => changes[i.product_id] === 'swap')
  const drop = plan.items.filter((i) => changes[i.product_id] === 'drop')
  const status = plan.status ?? 'pending'
  const actionable = status === 'pending' && !!onAction
  const over =
    Number(plan.total_annual_premium) > Number(plan.budget) ? '（超出预算）' : ''

  return (
    <Card
      size="small"
      title={
        <Flex gap={8} align="center">
          保障方案
          <Tag color={STATUS[status].color}>{STATUS[status].text}</Tag>
        </Flex>
      }
      extra={
        <Typography.Text>
          合计 <strong>{formatYuan(plan.total_annual_premium)}</strong>/年
          <Typography.Text type="secondary">
            {' '}
            · 预算 {formatYuan(plan.budget)}
            {over}
          </Typography.Text>
        </Typography.Text>
      }
      style={{ opacity: status === 'pending' || status === 'confirmed' ? 1 : 0.6 }}
    >
      <List<PlanItem>
        size="small"
        dataSource={plan.items}
        renderItem={(item) => (
          <List.Item
            extra={
              actionable ? (
                <Radio.Group
                  size="small"
                  optionType="button"
                  aria-label={`调整${item.product_name}`}
                  value={changes[item.product_id] ?? 'keep'}
                  onChange={(e) =>
                    setChanges((c) => {
                      const next = { ...c }
                      if (e.target.value === 'keep') delete next[item.product_id]
                      else next[item.product_id] = e.target.value as 'swap' | 'drop'
                      return next
                    })
                  }
                  options={[
                    { value: 'keep', label: '保留' },
                    { value: 'swap', label: '换一款' },
                    { value: 'drop', label: '不要这个险种' },
                  ]}
                />
              ) : undefined
            }
          >
            <List.Item.Meta
              title={
                <span>
                  <Tag>{item.category_label}</Tag>
                  {item.product_name}
                </span>
              }
              description={
                <>
                  <div>{itemDetail(item)}</div>
                  {item.lines.map((line) => (
                    <Typography.Text key={line.title} type="secondary" style={{ fontSize: 12 }}>
                      <div>
                        {line.title}：{line.basis}
                      </div>
                    </Typography.Text>
                  ))}
                </>
              }
            />
          </List.Item>
        )}
      />
      {plan.dropped.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 8 }}
          message="没有配上的险种"
          description={plan.dropped.map((d) => (
            <div key={d.category}>
              {d.category_label}：{d.reason}
            </div>
          ))}
        />
      )}
      {actionable && (
        <Flex gap={8} wrap style={{ marginTop: 12 }}>
          <Button type="primary" disabled={busy} onClick={() => onAction({ action: 'confirm' })}>
            确认方案
          </Button>
          <Button
            disabled={busy || (swap.length === 0 && drop.length === 0)}
            onClick={() =>
              onAction({
                action: 'edit',
                remove_product_ids: swap.map((i) => i.product_id),
                remove_categories: drop.map((i) => i.category),
              })
            }
          >
            按调整重新配置
          </Button>
          <Popconfirm title="取消这个方案？" onConfirm={() => onAction({ action: 'cancel' })}>
            <Button danger disabled={busy}>
              取消
            </Button>
          </Popconfirm>
          <Typography.Text type="secondary" style={{ alignSelf: 'center' }}>
            也可以直接在下方输入想怎么调整
          </Typography.Text>
        </Flex>
      )}
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
        保费由报价引擎按费率表计算；确认方案只是保存配置，实际投保以保险公司页面和核保结果为准。
      </Typography.Text>
    </Card>
  )
}
