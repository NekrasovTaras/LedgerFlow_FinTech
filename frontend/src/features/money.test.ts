import { describe, expect, it } from 'vitest'
import { money, parseRubles } from './money'
describe('exact monetary boundary conversions', () => {
  it.each([['0.01', 1], ['0.29', 29], ['1000', 100000], ['123,45', 12345], ['90071992547409.91', Number.MAX_SAFE_INTEGER]])('parses %s as exact integer minor units', (input, expected) => { expect(parseRubles(input)).toBe(expected) })
  it.each(['-1', '0', '1.234', '1e3', 'Infinity', 'NaN', '90071992547409.92', '', '1.2.3'])('rejects unsupported amount %s', (input) => { expect(() => parseRubles(input)).toThrow() })
  it('formats a large amount without losing the last minor unit', () => { expect(money(Number.MAX_SAFE_INTEGER)).toBe('90,071,992,547,409.91 ₽'); expect(money(-1)).toBe('−0.01 ₽') })
})
