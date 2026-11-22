import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo } from 'react'

import { productApi, profileApi } from '../lib/api'
import { newMemberId, toProfileData } from '../lib/profile'
import { queryKeys } from '../lib/queryKeys'
import {
  CATEGORY_LABELS,
  RELATION_LABELS,
  type FamilyMember,
  type FieldSource,
  type Profile,
  type ProfileData,
} from '../lib/types'

const GENDER_OPTIONS = [
  { value: 'male', label: '男' },
  { value: 'female', label: '女' },
]
const SS_OPTIONS = [
  { value: true, label: '有社保（医保）' },
  { value: false, label: '没有社保' },
]
const RELATION_OPTIONS = Object.entries(RELATION_LABELS).map(([value, label]) => ({ value, label }))
const OCCUPATION_OPTIONS = [1, 2, 3, 4, 5, 6].map((n) => ({
  value: n,
  label: `${n} 类${n === 1 ? '（如内勤、办公室职员）' : n >= 5 ? '（高风险职业）' : ''}`,
}))

function SourceTag({ source }: { source?: FieldSource }) {
  if (source === 'chat') return <Tag color="green">来自对话</Tag>
  if (source === 'chat_age') return <Tag color="orange">按对话中的年龄估算</Tag>
  return null
}

/** 我的画像（spec D8、D21）：配置方案、理赔咨询时自动带入。 */
export function ProfilePage() {
  const qc = useQueryClient()
  const { message } = App.useApp()
  const [form] = Form.useForm<ProfileData>()
  const profile = useQuery({ queryKey: queryKeys.profile, queryFn: profileApi.get })
  // 产品库只有十几款，一次取完给「已有保障」选择
  const products = useQuery({
    queryKey: ['products', 'all'],
    queryFn: () => productApi.list({ pageSize: 50 }),
  })
  const family = Form.useWatch('family', form) as FamilyMember[] | undefined

  useEffect(() => {
    if (profile.data) form.setFieldsValue(profile.data)
  }, [profile.data, form])

  const save = useMutation({
    mutationFn: (values: ProfileData) => profileApi.save(toProfileData(values, products.data?.items)),
    onSuccess: (saved: Profile) => {
      qc.setQueryData(queryKeys.profile, saved)
      message.success('画像已保存')
    },
    onError: (e) => message.error(e.message),
  })

  const insuredOptions = useMemo(
    () => [
      { value: 'self', label: '本人' },
      ...(family ?? [])
        .filter((m) => m?.id)
        .map((m) => ({ value: m.id, label: m.name || RELATION_LABELS[m.relation] || '家人' })),
    ],
    [family],
  )
  const productOptions = (products.data?.items ?? []).map((p) => ({
    value: p.id,
    label: `${p.name}（${CATEGORY_LABELS[p.category]}）`,
  }))

  if (profile.isPending) return <Skeleton active paragraph={{ rows: 8 }} />
  if (profile.isError)
    return <Alert type="error" showIcon message="画像加载失败" description={profile.error.message} />

  const sources = profile.data.field_sources

  return (
    <Flex vertical gap={16} style={{ maxWidth: 880 }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        我的画像
      </Typography.Title>
      <Typography.Text type="secondary">
        配置保障方案时会自动带入年龄、性别、社保和预算；咨询理赔时会按这里登记的已有保障查条款。
        对话里提到的信息需要您点「保存到画像」才会写入。
        {profile.data.updated_at &&
          ` 最近更新：${new Date(profile.data.updated_at).toLocaleString('zh-CN')}`}
      </Typography.Text>

      <Form<ProfileData>
        form={form}
        layout="vertical"
        initialValues={profile.data}
        onFinish={(v) => save.mutate(v)}
      >
        <Card title="基本信息" size="small" style={{ marginBottom: 16 }}>
          <Flex wrap gap={16}>
            <Form.Item
              name="birth_date"
              label={
                <span>
                  出生日期 <SourceTag source={sources.birth_date} />
                  {profile.data.age !== null && (
                    <Typography.Text type="secondary">（{profile.data.age} 周岁）</Typography.Text>
                  )}
                </span>
              }
              style={{ width: 240 }}
            >
              <Input type="date" aria-label="出生日期" />
            </Form.Item>
            <Form.Item
              name="gender"
              label={<span>性别 <SourceTag source={sources.gender} /></span>}
              style={{ width: 160 }}
            >
              <Select allowClear options={GENDER_OPTIONS} aria-label="性别" />
            </Form.Item>
            <Form.Item
              name="social_security"
              label={<span>社保 <SourceTag source={sources.social_security} /></span>}
              style={{ width: 200 }}
            >
              <Select allowClear options={SS_OPTIONS} aria-label="社保" />
            </Form.Item>
            <Form.Item name="occupation_class" label="职业类别" style={{ width: 240 }}>
              <Select allowClear options={OCCUPATION_OPTIONS} aria-label="职业类别" />
            </Form.Item>
            <Form.Item
              name="city"
              label={<span>城市 <SourceTag source={sources.city} /></span>}
              style={{ width: 200 }}
            >
              <Input maxLength={32} aria-label="城市" />
            </Form.Item>
            <Form.Item
              name="annual_budget"
              label={<span>每年保费预算（元） <SourceTag source={sources.annual_budget} /></span>}
              style={{ width: 240 }}
            >
              <InputNumber<string>
                stringMode
                min="0"
                max="10000000"
                precision={2}
                style={{ width: '100%' }}
                aria-label="每年保费预算"
              />
            </Form.Item>
          </Flex>
        </Card>

        <Card
          title={<span>家庭成员 <SourceTag source={sources.family} /></span>}
          size="small"
          style={{ marginBottom: 16 }}
        >
          <Form.List name="family">
            {(fields, { add, remove }) => (
              <Flex vertical gap={8}>
                {fields.map((f) => {
                  const estimated = form.getFieldValue(['family', f.name, 'birth_date_estimated'])
                  return (
                    <Flex key={f.key} wrap gap={8} align="flex-start">
                      <Form.Item name={[f.name, 'id']} hidden>
                        <Input />
                      </Form.Item>
                      <Form.Item name={[f.name, 'birth_date_estimated']} hidden>
                        <Input />
                      </Form.Item>
                      <Form.Item name={[f.name, 'relation']} rules={[{ required: true, message: '请选择关系' }]}>
                        <Select options={RELATION_OPTIONS} placeholder="关系" style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item name={[f.name, 'name']}>
                        <Input placeholder="称呼（可选）" maxLength={20} style={{ width: 130 }} />
                      </Form.Item>
                      <Form.Item
                        name={[f.name, 'birth_date']}
                        extra={estimated ? '按对话中的年龄估算，可改成准确日期' : undefined}
                      >
                        <Input type="date" aria-label="成员出生日期" style={{ width: 160 }} />
                      </Form.Item>
                      <Form.Item name={[f.name, 'gender']}>
                        <Select allowClear options={GENDER_OPTIONS} placeholder="性别" style={{ width: 90 }} />
                      </Form.Item>
                      <Form.Item name={[f.name, 'social_security']}>
                        <Select allowClear options={SS_OPTIONS} placeholder="社保" style={{ width: 150 }} />
                      </Form.Item>
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        aria-label="删除成员"
                        onClick={() => remove(f.name)}
                      />
                    </Flex>
                  )
                })}
                <Button
                  icon={<PlusOutlined />}
                  style={{ alignSelf: 'flex-start' }}
                  disabled={fields.length >= 10}
                  onClick={() =>
                    add({
                      id: newMemberId(),
                      relation: 'child',
                      name: '',
                      birth_date: null,
                      birth_date_estimated: false,
                      gender: null,
                      social_security: null,
                    } satisfies FamilyMember)
                  }
                >
                  添加家庭成员
                </Button>
              </Flex>
            )}
          </Form.List>
        </Card>

        <Card
          title={<span>已有保障 <SourceTag source={sources.existing_coverage} /></span>}
          size="small"
          style={{ marginBottom: 16 }}
        >
          <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
            选了本平台的产品，理赔咨询时会按它的条款回答；其他公司的产品只能给通用的理赔指引。
          </Typography.Paragraph>
          <Form.List name="existing_coverage">
            {(fields, { add, remove }) => (
              <Flex vertical gap={8}>
                {fields.map((f) => (
                  <Flex key={f.key} wrap gap={8} align="flex-start">
                    <Form.Item name={[f.name, 'insured']} rules={[{ required: true, message: '请选择被保险人' }]}>
                      <Select options={insuredOptions} placeholder="被保险人" style={{ width: 110 }} />
                    </Form.Item>
                    <Form.Item name={[f.name, 'product_id']}>
                      <Select
                        allowClear
                        showSearch={{ optionFilterProp: 'label' }}
                        options={productOptions}
                        placeholder="本平台产品（其他公司的不选）"
                        style={{ width: 280 }}
                      />
                    </Form.Item>
                    <Form.Item noStyle dependencies={[['existing_coverage', f.name, 'product_id']]}>
                      {() =>
                        form.getFieldValue(['existing_coverage', f.name, 'product_id']) ? null : (
                          <Form.Item
                            name={[f.name, 'product_name']}
                            rules={[{ required: true, message: '请填写产品名' }]}
                          >
                            <Input placeholder="产品名" maxLength={50} style={{ width: 180 }} />
                          </Form.Item>
                        )
                      }
                    </Form.Item>
                    <Form.Item name={[f.name, 'sum_assured']}>
                      <InputNumber<string>
                        stringMode
                        min="0"
                        precision={2}
                        placeholder="保额（元）"
                        style={{ width: 140 }}
                      />
                    </Form.Item>
                    <Form.Item name={[f.name, 'start_date']} tooltip="生效日期">
                      <Input type="date" aria-label="生效日期" style={{ width: 160 }} />
                    </Form.Item>
                    <Button
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      aria-label="删除保障"
                      onClick={() => remove(f.name)}
                    />
                  </Flex>
                ))}
                <Button
                  icon={<PlusOutlined />}
                  style={{ alignSelf: 'flex-start' }}
                  disabled={fields.length >= 20}
                  onClick={() =>
                    add({
                      insured: 'self',
                      product_id: null,
                      product_name: '',
                      category: null,
                      sum_assured: null,
                      start_date: null,
                    })
                  }
                >
                  登记已有保障
                </Button>
              </Flex>
            )}
          </Form.List>
        </Card>

        <Button type="primary" htmlType="submit" loading={save.isPending}>
          保存画像
        </Button>
      </Form>
    </Flex>
  )
}
