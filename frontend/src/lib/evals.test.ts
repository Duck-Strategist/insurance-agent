import { humanAgreements } from './evals'

test('humanAgreements 列出每一批人工标注，测试集在开发集前面', () => {
  const got = humanAgreements({
    n: 86,
    human_dev: { correctness_agreement: 0.94 },
    human_test: { correctness_agreement: 0.88 },
    human: { correctness_agreement: 0.76 },
    human_note: 'x',
  })
  expect(got.map((g) => [g.key, g.label])).toEqual([
    ['human', '抽检'],
    ['human_test', '测试集'],
    ['human_dev', '开发集'],
  ])
  expect(humanAgreements({ n: 1 })).toEqual([])
})
