import { DeleteOutlined, EditOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  List,
  Modal,
  Popconfirm,
  Skeleton,
  Space,
  Typography,
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { ChatMessageView } from '../components/ChatMessageView'
import { chatApi } from '../lib/api'
import { visibleHistory } from '../lib/answer'
import { planActionLabel } from '../lib/quote'
import { queryKeys } from '../lib/queryKeys'
import type { ChatEvent, ChatThread, PlanAction, PlanCardData } from '../lib/types'
import { useChatStream } from '../stores/chat'

const SUGGESTIONS = [
  '我 30 岁，有社保，预算每年 5000，帮我配一份保险',
  '众安尊享e生百万医疗险的等待期多久？',
  '达尔文12号得了重疾以后还要交保费吗？',
  '小蜜蜂6号意外险，猝死能赔吗？',
]

function ThreadList({ current }: { current?: string }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [renaming, setRenaming] = useState<ChatThread | null>(null)
  const [title, setTitle] = useState('')
  const threads = useQuery({ queryKey: queryKeys.threads, queryFn: chatApi.threads })

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => chatApi.rename(id, title),
    onSuccess: () => {
      setRenaming(null)
      void qc.invalidateQueries({ queryKey: queryKeys.threads })
    },
    onError: (e) => message.error(e.message),
  })
  const remove = useMutation({
    mutationFn: chatApi.remove,
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: queryKeys.threadMessages(id) })
      void qc.invalidateQueries({ queryKey: queryKeys.threads })
      if (id === current) navigate('/chat', { replace: true })
    },
    onError: (e) => message.error(e.message),
  })

  return (
    <Card
      size="small"
      title="我的对话"
      extra={
        <Button size="small" icon={<PlusOutlined />} onClick={() => navigate('/chat')}>
          新对话
        </Button>
      }
      style={{ width: 260, flex: 'none', alignSelf: 'stretch', overflow: 'auto' }}
    >
      {threads.isError ? (
        <Alert type="error" message={threads.error.message} />
      ) : (
        <List<ChatThread>
          size="small"
          loading={threads.isPending}
          dataSource={threads.data}
          locale={{ emptyText: '还没有对话' }}
          renderItem={(t) => (
            <List.Item
              style={{
                cursor: 'pointer',
                background: t.id === current ? '#e6f4ff' : undefined,
                paddingInline: 8,
                borderRadius: 6,
              }}
              onClick={() => navigate(`/chat/${t.id}`)}
              actions={[
                <EditOutlined
                  key="rename"
                  aria-label="重命名"
                  onClick={(e) => {
                    e.stopPropagation()
                    setTitle(t.title)
                    setRenaming(t)
                  }}
                />,
                <Popconfirm
                  key="delete"
                  title="删除这个对话？"
                  description="对话记录会一起删除，无法恢复"
                  onConfirm={(e) => {
                    e?.stopPropagation()
                    remove.mutate(t.id)
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <DeleteOutlined aria-label="删除" onClick={(e) => e.stopPropagation()} />
                </Popconfirm>,
              ]}
            >
              <Typography.Text ellipsis style={{ maxWidth: 150 }}>
                {t.title}
              </Typography.Text>
            </List.Item>
          )}
        />
      )}
      <Modal
        title="重命名对话"
        open={renaming !== null}
        confirmLoading={rename.isPending}
        okButtonProps={{ disabled: !title.trim() }}
        onOk={() => renaming && rename.mutate({ id: renaming.id, title: title.trim() })}
        onCancel={() => setRenaming(null)}
      >
        <Input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
      </Modal>
    </Card>
  )
}

export function ChatPage() {
  const { threadId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const stream = useChatStream()
  const [input, setInput] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const messages = useQuery({
    queryKey: queryKeys.threadMessages(threadId ?? ''),
    queryFn: () => chatApi.messages(threadId!),
    enabled: !!threadId,
  })

  // 进行中的这一轮只在它所属的会话里显示（新会话在拿到 id 之前属于 /chat）
  const showStream =
    (stream.streaming || stream.error !== null || stream.answer !== '') &&
    stream.threadId === (threadId ?? null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.data, stream.answer, stream.status])

  // 离开页面（含退出登录被跳到登录页）时停止生成并清空进行中的内容：
  // 后端会随连接关闭而取消；不清空的话下一个登录的用户会看到上一个人的提问
  useEffect(
    () => () => {
      abortRef.current?.abort()
      useChatStream.getState().reset()
    },
    [],
  )

  /** 跑一轮流式回答：发消息和点方案卡片按钮共用（question 是显示在右侧的那句话） */
  async function run(
    question: string,
    open: (signal: AbortSignal) => AsyncGenerator<ChatEvent>,
  ) {
    const controller = new AbortController()
    abortRef.current = controller
    const { start, setThread, appendAnswer, setStatus, setCitations, setPlan, fail, finish, reset } =
      useChatStream.getState()
    start(threadId ?? null, question)
    let current = threadId
    try {
      for await (const ev of open(controller.signal)) {
        if (ev.type === 'thread') {
          current = ev.id
          setThread(ev.id)
          if (!threadId) navigate(`/chat/${ev.id}`, { replace: true })
          void qc.invalidateQueries({ queryKey: queryKeys.threads })
        } else if (ev.type === 'status') setStatus(ev.text)
        else if (ev.type === 'token') appendAnswer(ev.text)
        else if (ev.type === 'citations') setCitations(ev.items)
        else if (ev.type === 'plan_card') setPlan(ev.plan)
        else if (ev.type === 'plan_status') void qc.invalidateQueries({ queryKey: queryKeys.plans })
        else if (ev.type === 'error') {
          fail(ev.message)
          return
        }
      }
      finish()
      // 先拉到含本轮的历史，再清掉流式内容，避免回答闪一下
      if (current) await qc.invalidateQueries({ queryKey: queryKeys.threadMessages(current) })
      reset()
      void qc.invalidateQueries({ queryKey: queryKeys.threads })
      void qc.invalidateQueries({ queryKey: queryKeys.plans })
    } catch (e) {
      if (controller.signal.aborted) {
        reset()
        if (current) void qc.invalidateQueries({ queryKey: queryKeys.threadMessages(current) })
      } else {
        fail(e instanceof Error ? e.message : '发送失败')
      }
    }
  }

  async function send(text: string) {
    const question = text.trim()
    if (!question || stream.streaming) return
    setInput('')
    await run(question, (signal) =>
      chatApi.stream({ thread_id: threadId, message: question }, signal),
    )
  }

  async function planAction(plan: PlanCardData, action: PlanAction) {
    if (!threadId || stream.streaming) return
    const label = planActionLabel(plan, action)
    await run(label, (signal) =>
      chatApi.resume({ thread_id: threadId, plan_id: plan.plan_id, ...action }, signal),
    )
  }

  const history = visibleHistory(threadId ? (messages.data ?? []) : [], showStream, stream.question)
  const empty = !threadId && !showStream

  return (
    <Flex gap={16} style={{ height: 'calc(100vh - 64px - 48px)', minHeight: 480 }}>
      <ThreadList current={threadId} />
      <Flex vertical style={{ flex: 1, minWidth: 0 }} gap={12}>
        <Flex vertical gap={16} style={{ flex: 1, overflow: 'auto', padding: '4px 4px 0' }}>
          {messages.isError ? (
            <Alert type="error" showIcon message={messages.error.message} />
          ) : threadId && messages.isPending ? (
            <Skeleton active />
          ) : empty ? (
            <Flex vertical align="center" justify="center" gap={16} style={{ flex: 1 }}>
              <Empty description="可以问产品条款（回答标注出处），也可以让我按预算配置保障方案" />
              <Space wrap style={{ justifyContent: 'center' }}>
                {SUGGESTIONS.map((s) => (
                  <Button key={s} onClick={() => void send(s)}>
                    {s}
                  </Button>
                ))}
              </Space>
            </Flex>
          ) : (
            history.map((m, i) => (
              <ChatMessageView
                key={m.id ?? i}
                role={m.role}
                content={m.content}
                citations={m.citations}
                plan={m.plan}
                planBusy={stream.streaming}
                onPlanAction={(plan, action) => void planAction(plan, action)}
              />
            ))
          )}
          {showStream && (
            <>
              <ChatMessageView role="user" content={stream.question} />
              {stream.error ? (
                <Alert type="error" showIcon message={stream.error} />
              ) : (
                <ChatMessageView
                  role="assistant"
                  content={stream.answer}
                  citations={stream.citations}
                  plan={stream.plan}
                  planBusy
                  status={stream.status ?? (stream.answer ? null : '思考中…')}
                />
              )}
            </>
          )}
          <div ref={bottomRef} />
        </Flex>
        <Flex gap={8} align="flex-end">
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={(e) => {
              // Enter 发送，Shift+Enter 换行；输入法组字时的回车不算
              if (!e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void send(input)
              }
            }}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            autoSize={{ minRows: 1, maxRows: 5 }}
            maxLength={1000}
            disabled={stream.streaming}
          />
          {stream.streaming ? (
            <Button onClick={() => abortRef.current?.abort()}>停止</Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              disabled={!input.trim()}
              onClick={() => void send(input)}
            >
              发送
            </Button>
          )}
        </Flex>
      </Flex>
    </Flex>
  )
}
