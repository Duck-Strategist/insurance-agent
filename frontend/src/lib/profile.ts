import type { Coverage, FamilyMember, Product, ProfileData } from './types'

/** 家庭成员 id：已有保障用它指向被保险人（不用下标，删除成员不会错位）；格式与后端一致 */
export function newMemberId(): string {
  return crypto.randomUUID().replace(/-/g, '').slice(0, 8)
}

const blank = <T>(v: T | '' | undefined): T | null => (v === '' || v === undefined ? null : v)

/**
 * 表单值 → 提交给 PUT /profile 的整份画像：空字符串、未填的字段统一成 null；
 * 选了本平台产品时产品名和险种取产品库里的（服务端也会以库为准再填一次）。
 */
export function toProfileData(values: Partial<ProfileData>, products: Product[] = []): ProfileData {
  const byId = new Map(products.map((p) => [p.id, p]))
  return {
    birth_date: blank(values.birth_date),
    gender: blank(values.gender),
    social_security: blank(values.social_security),
    occupation_class: blank(values.occupation_class),
    city: blank(values.city?.trim()),
    annual_budget: blank(values.annual_budget),
    family: (values.family ?? []).map(
      (m): FamilyMember => ({
        id: m.id,
        relation: m.relation,
        name: m.name?.trim() ?? '',
        birth_date: blank(m.birth_date),
        birth_date_estimated: Boolean(m.birth_date_estimated),
        gender: blank(m.gender),
        social_security: blank(m.social_security),
      }),
    ),
    existing_coverage: (values.existing_coverage ?? []).map((c): Coverage => {
      const product = c.product_id ? byId.get(c.product_id) : undefined
      return {
        insured: c.insured,
        product_id: blank(c.product_id),
        product_name: product?.name ?? c.product_name?.trim() ?? '',
        category: product?.category ?? blank(c.category),
        sum_assured: blank(c.sum_assured),
        start_date: blank(c.start_date),
      }
    }),
  }
}
