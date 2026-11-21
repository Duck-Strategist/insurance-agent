import { parsePage, safeRedirect } from './navigation'

test.each([
  [null, 1],
  ['', 1],
  ['abc', 1],
  ['0', 1],
  ['-3', 1],
  ['2.5', 2],
  ['3', 3],
  ['Infinity', 1],
])('parsePage(%s) = %s', (raw, expected) => {
  expect(parsePage(raw)).toBe(expected)
})

test.each([
  ['/products?category=life', '/products?category=life'],
  ['/products/1#top', '/products/1#top'],
  ['https://evil.com', '/'],
  ['//evil.com', '/'],
  ['/\\evil.com', '/'],
  ['/\\/evil.com', '/'],
  ['javascript:alert(1)', '/'],
  [undefined, '/'],
  [42, '/'],
])('safeRedirect(%j) = %s', (from, expected) => {
  expect(safeRedirect(from)).toBe(expected)
})
