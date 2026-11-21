/**
 * 约定：属于当前用户的数据，queryKey 一律以 USER_SCOPE 开头（如 [USER_SCOPE, 'threads']）。
 * 登录身份变化（退出、换号、token 失效被自动退出）时，Providers 会统一清掉这些缓存，
 * 避免下一个用户看到上一个用户的数据。公开数据（产品等）不受影响。
 */
export const USER_SCOPE = 'me'

export const queryKeys = {
  currentUser: [USER_SCOPE, 'user'] as const,
}
