import { newMemberId, toProfileData } from './profile'
import type { Product } from './types'

test('newMemberId 符合后端格式', () => {
  expect(newMemberId()).toMatch(/^[a-z0-9]{8}$/)
})

test('toProfileData：空值统一成 null，库内产品的名字和险种以产品库为准', () => {
  const products = [{ id: 3, name: '达尔文12号', category: 'critical_illness' } as Product]
  const data = toProfileData(
    {
      birth_date: '',
      city: '  杭州 ',
      annual_budget: '5000',
      family: [
        { id: 'w1', relation: 'spouse', name: undefined as unknown as string, birth_date: '',
          birth_date_estimated: undefined as unknown as boolean, gender: null, social_security: false },
      ],
      existing_coverage: [
        { insured: 'w1', product_id: 3, product_name: '', category: null, sum_assured: null, start_date: '' },
        { insured: 'self', product_id: null, product_name: ' 团险 ', category: null, sum_assured: '1000',
          start_date: '2025-01-01' },
      ],
    },
    products,
  )
  expect(data).toEqual({
    birth_date: null,
    gender: null,
    social_security: null,
    occupation_class: null,
    city: '杭州',
    annual_budget: '5000',
    family: [
      { id: 'w1', relation: 'spouse', name: '', birth_date: null, birth_date_estimated: false,
        gender: null, social_security: false },
    ],
    existing_coverage: [
      { insured: 'w1', product_id: 3, product_name: '达尔文12号', category: 'critical_illness',
        sum_assured: null, start_date: null },
      { insured: 'self', product_id: null, product_name: '团险', category: null, sum_assured: '1000',
        start_date: '2025-01-01' },
    ],
  })
})
