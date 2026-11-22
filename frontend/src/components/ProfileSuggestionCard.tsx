import { IdcardOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, Card, Descriptions, Flex, Typography } from 'antd'
import { Link } from 'react-router'

import { profileApi } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import type { ProfileSuggestion } from '../lib/types'

/**
 * 对话里抽取出的画像信息（spec D20）：用户点「保存到画像」才写库，忽略就丢掉。
 * 保存走增量合并接口，不会覆盖画像里这次没提到的内容。
 */
export function ProfileSuggestionCard({
  suggestion,
  onDone,
}: {
  suggestion: ProfileSuggestion
  onDone: () => void
}) {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const save = useMutation({
    mutationFn: () => profileApi.applySuggestion(suggestion.patch),
    onSuccess: (profile) => {
      qc.setQueryData(queryKeys.profile, profile)
      message.success('已保存到画像')
      onDone()
    },
    onError: (e) => message.error(e.message),
  })

  return (
    <Card
      size="small"
      style={{ alignSelf: 'center', width: 'min(520px, 100%)', background: '#f6ffed' }}
      title={
        <Flex gap={8} align="center">
          <IdcardOutlined />
          <span>保存到画像？</span>
        </Flex>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        从对话里记下了这些信息。保存后，下次配置方案、咨询理赔时会自动带入，可以在
        <Link to="/profile">我的画像</Link>里随时修改。
      </Typography.Paragraph>
      <Descriptions
        size="small"
        column={1}
        items={suggestion.items.map((it, i) => ({ key: i, label: it.label, children: it.value }))}
      />
      <Flex gap={8} justify="flex-end" style={{ marginTop: 12 }}>
        <Button onClick={onDone} disabled={save.isPending}>
          忽略
        </Button>
        <Button type="primary" loading={save.isPending} onClick={() => save.mutate()}>
          保存到画像
        </Button>
      </Flex>
    </Card>
  )
}
