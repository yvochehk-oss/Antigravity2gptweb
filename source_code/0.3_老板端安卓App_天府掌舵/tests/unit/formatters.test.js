import { describe, expect, it } from 'vitest'
import { formatCurrency, formatMoney, formatPercent } from '../../src/utils/formatters'

describe('formatters', () => {
  it('switches to 亿 when amount reaches 100 million', () => {
    expect(formatMoney(100_000_000)).toBe('¥1.00 亿')
    expect(formatMoney(258_400_000)).toBe('¥2.58 亿')
  })

  it('switches to 万 when amount is between ten thousand and one hundred million', () => {
    expect(formatMoney(150_000)).toBe('¥15.00 万')
    expect(formatMoney(9_999)).not.toContain('万')
  })

  it('uses locale grouping with two decimals for ordinary amounts', () => {
    expect(formatMoney(5_000)).toBe('¥5,000.00')
    expect(formatMoney(0)).toBe('¥0.00')
    expect(formatMoney(null)).toBe('—')
  })

  it('formats project gross-margin and collection ratios to exactly two decimals', () => {
    expect(formatPercent(0.123456)).toBe('12.35%')
    expect(formatPercent(0.987654)).toBe('98.77%')
    expect(formatPercent(12.3456)).toBe('12.35%')
    expect(formatPercent(0.2095271951793543)).toBe('20.95%')
    expect(formatPercent(1.08999999999806258)).toBe('1.09%')
    expect(formatPercent(null)).toBe('—')
  })

  it('prepends a currency symbol via formatCurrency', () => {
    expect(formatCurrency(12_345.6)).toBe('¥12,345.60')
    expect(formatCurrency(7_890, false, { symbol: '$' })).toBe('$7,890.00')
    expect(formatCurrency(7_890, true)).toBe('¥ ***,***')
  })
})