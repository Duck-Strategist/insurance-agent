import { RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Avatar, Collapse, Flex, Tag, Tooltip, Typography } from 'antd'
import { Fragment, useMemo, useState } from 'react'

import { splitLine } from '../lib/answer'
import type { Citation, PlanAction, PlanCardData } from '../lib/types'
import { PlanCard } from './PlanCard'

function AnswerText({
  text,
  citations,
  onCite,
}: {
  text: string
  citations: Citation[]
  onCite: (index: number) => void
}) {
  const valid = useMemo(() => new Set(citations.map((c) => c.index)), [citations])
  const byIndex = useMemo(() => new Map(citations.map((c) => [c.index, c])), [citations])
  return (
    <div style={{ lineHeight: 1.8 }}>
      {text.split('\n').map((line, i) => (
        <div key={i} style={{ minHeight: line.trim() ? undefined : 8 }}>
          {splitLine(line, valid).map((seg, j) => {
            if (seg.kind === 'bold') return <strong key={j}>{seg.text}</strong>
            if (seg.kind === 'text') return <Fragment key={j}>{seg.text}</Fragment>
            const c = byIndex.get(seg.index)
            return (
              <Tooltip key={j} title={c ? `${c.doc_title} · ${c.section_path.join(' › ')}` : ''}>
                <Tag
                  color="blue"
                  role="button"
                  aria-label={`查看出处 ${seg.index}`}
                  style={{ cursor: 'pointer', marginInline: 2, paddingInline: 4 }}
                  onClick={() => onCite(seg.index)}
                >
                  {seg.index}
                </Tag>
              </Tooltip>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function CitationList({
  citations,
  active,
  onChange,
}: {
  citations: Citation[]
  active: string[]
  onChange: (keys: string[]) => void
}) {
  return (
    <Collapse
      size="small"
      activeKey={active}
      onChange={(keys) => onChange(Array.isArray(keys) ? keys : [keys])}
      items={citations.map((c) => ({
        key: String(c.index),
        label: (
          <Typography.Text>
            <Tag color="blue">{c.index}</Tag>
            {c.doc_title}
            <Typography.Text type="secondary"> · {c.section_path.join(' › ') || '（无标题）'}</Typography.Text>
          </Typography.Text>
        ),
        children: (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0, fontSize: 13 }}>
            {c.parent_content}
          </Typography.Paragraph>
        ),
      }))}
    />
  )
}

export function ChatMessageView({
  role,
  content,
  citations = [],
  status,
  plan,
  planBusy,
  onPlanAction,
}: {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  /** 生成中的状态提示（正在检索条款…） */
  status?: string | null
  /** 顾问方案卡片 */
  plan?: PlanCardData | null
  planBusy?: boolean
  onPlanAction?: (plan: PlanCardData, action: PlanAction) => void
}) {
  const [active, setActive] = useState<string[]>([])
  const isUser = role === 'user'
  return (
    <Flex gap={12} justify={isUser ? 'flex-end' : 'flex-start'} align="flex-start">
      {!isUser && <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff', flex: 'none' }} />}
      <Flex vertical gap={8} style={{ maxWidth: isUser ? '70%' : '85%', minWidth: 0 }}>
        <div
          style={{
            background: isUser ? '#1677ff' : '#fff',
            color: isUser ? '#fff' : undefined,
            border: isUser ? undefined : '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '8px 12px',
            whiteSpace: isUser ? 'pre-wrap' : undefined,
            wordBreak: 'break-word',
          }}
        >
          {isUser ? (
            content
          ) : (
            <>
              {content && (
                <AnswerText
                  text={content}
                  citations={citations}
                  onCite={(i) => setActive((keys) => [...new Set([...keys, String(i)])])}
                />
              )}
              {status && <Typography.Text type="secondary">{status}</Typography.Text>}
            </>
          )}
        </div>
        {!isUser && citations.length > 0 && (
          <CitationList citations={citations} active={active} onChange={setActive} />
        )}
        {!isUser && plan && (
          <PlanCard
            plan={plan}
            busy={planBusy}
            onAction={onPlanAction ? (action) => onPlanAction(plan, action) : undefined}
          />
        )}
      </Flex>
      {isUser && <Avatar icon={<UserOutlined />} style={{ flex: 'none' }} />}
    </Flex>
  )
}
