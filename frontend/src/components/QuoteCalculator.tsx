import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Checkbox,
  Descriptions,
  Flex,
  Form,
  Input,
  InputNumber,
  List,
  Radio,
  Skeleton,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link, useLocation } from 'react-router'

import { pricingApi } from '../lib/api'
import { formatYuan } from '../lib/format'
import { queryKeys } from '../lib/queryKeys'
import { toQuoteParams } from '../lib/quote'
import type { QuoteParamSpec, QuoteResult } from '../lib/types'
import { useAuthStore } from '../stores/auth'

function ParamField({ spec }: { spec: QuoteParamSpec }) {
  const rules = spec.required ? [{ required: true, message: `请填写${spec.label}` }] : []
  const common = { name: spec.name, label: spec.label, rules, extra: spec.hint || undefined }
  switch (spec.kind) {
    case 'age':
      return (
        <Form.Item {...common}>
          {/* 不按投保范围限制输入：InputNumber 会把超出的值悄悄改成上限，报出别的年龄的价格。
              超出范围交给后端返回「不可投保」和原因，范围写在提示里 */}
          <InputNumber
            min={0}
            max={120}
            precision={0}
            addonAfter="周岁"
            style={{ width: 180 }}
          />
        </Form.Item>
      )
    case 'bool':
      return (
        <Form.Item {...common}>
          {/* 用字符串取值：布尔值会变成单选框的无障碍名称（读屏读出 "true"） */}
          <Radio.Group
            options={[
              { value: 'yes', label: '是' },
              { value: 'no', label: '否' },
            ]}
          />
        </Form.Item>
      )
    case 'choice':
      return (
        <Form.Item {...common}>
          {/* 非必填的单选加一个「不选」：单选按钮选中后无法取消，提交时空值会被去掉 */}
          <Radio.Group
            options={spec.required ? spec.choices : [{ value: '', label: '不选' }, ...spec.choices]}
            optionType="button"
          />
        </Form.Item>
      )
    case 'multi':
      return (
        <Form.Item {...common}>
          <Checkbox.Group options={spec.choices} />
        </Form.Item>
      )
    case 'money':
      return (
        <Form.Item
          {...common}
          rules={[
            ...rules,
            {
              // 只用于校验，不改表单里的值；提交时 toQuoteParams 同样会去空格
              transform: (v: unknown) => (typeof v === 'string' ? v.trim() : v),
              pattern: /^\d+(\.\d{1,2})?$/,
              message: '请输入金额，最多两位小数',
            },
          ]}
        >
          {/* 金额用字符串提交，避免浮点误差 */}
          <Input addonAfter="元" style={{ width: 220 }} inputMode="decimal" />
        </Form.Item>
      )
  }
}

function QuoteView({ result }: { result: QuoteResult }) {
  if (result.status !== 'ok') {
    return (
      <Alert
        type="warning"
        showIcon
        message={result.status === 'missing_params' ? '还缺少参数' : '无法报价'}
        description={result.messages.join('；') || result.missing.join('、')}
      />
    )
  }
  const items = [
    { key: 'annual', label: '年交保费', children: formatYuan(result.annual_premium!) },
    { key: 'years', label: '交费年数', children: `${result.pay_years} 年` },
    { key: 'total', label: '总保费', children: formatYuan(result.total_premium!) },
  ]
  if (result.sum_assured) {
    items.push({ key: 'sa', label: '基本保额', children: formatYuan(result.sum_assured) })
  }
  return (
    <Flex vertical gap={8}>
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }} items={items} />
      <List
        size="small"
        header={<Typography.Text strong>计算明细</Typography.Text>}
        dataSource={result.lines}
        renderItem={(line) => (
          <List.Item extra={formatYuan(line.premium)}>
            <List.Item.Meta title={line.title} description={line.basis} />
          </List.Item>
        )}
      />
      <Typography.Text type="secondary">费率版本 {result.rate_version}</Typography.Text>
    </Flex>
  )
}

/** 按产品的报价参数动态生成表单；数字全部由后端按费率表计算。 */
export function QuoteCalculator({ productId }: { productId: number }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const token = useAuthStore((s) => s.token)
  const location = useLocation()
  const [form] = Form.useForm()
  const [result, setResult] = useState<QuoteResult | null>(null)

  const info = useQuery({
    queryKey: ['pricing', productId],
    queryFn: () => pricingApi.info(productId),
  })

  const calculate = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      pricingApi.calculate(productId, toQuoteParams(info.data!.params, values)),
    onSuccess: setResult,
    onError: (e) => message.error(e.message),
  })

  const save = useMutation({
    mutationFn: () =>
      pricingApi.save(productId, toQuoteParams(info.data!.params, form.getFieldsValue())),
    onSuccess: () => {
      message.success('报价已保存')
      void queryClient.invalidateQueries({ queryKey: queryKeys.quotes })
    },
    onError: (e) => message.error(e.message),
  })

  if (info.isPending) return <Skeleton active />
  if (info.isError) return <Alert type="error" showIcon message={info.error.message} />
  if (!info.data.supported) {
    return <Typography.Text type="secondary">该产品暂不支持在线报价。</Typography.Text>
  }

  return (
    <Flex vertical gap={16}>
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => calculate.mutate(values)}
        // 参数改了，旧结果就不再对应当前表单
        onValuesChange={() => setResult(null)}
      >
        {info.data.params.map((spec) => (
          <ParamField key={spec.name} spec={spec} />
        ))}
        <Button type="primary" htmlType="submit" loading={calculate.isPending}>
          计算保费
        </Button>
      </Form>
      {result && <QuoteView result={result} />}
      {result?.status === 'ok' &&
        (token ? (
          <div>
            <Button onClick={() => save.mutate()} loading={save.isPending}>
              保存报价
            </Button>{' '}
            <Link to="/quotes">查看我的报价</Link>
          </div>
        ) : (
          <Typography.Text type="secondary">
            <Link to="/login" state={{ from: location.pathname }}>
              登录
            </Link>
            后可保存报价
          </Typography.Text>
        ))}
    </Flex>
  )
}
