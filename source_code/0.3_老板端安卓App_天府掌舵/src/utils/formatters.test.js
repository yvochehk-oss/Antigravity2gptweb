import { describe, expect, it } from 'vitest'

import { formatCurrency, formatMoney, formatPercent } from './formatters'

describe('executive numeric formatters', () => {
  it('keeps missing and invalid facts unavailable instead of rendering zero', () => {
    for (const value of [null, undefined, '', Number.NaN]) {
      expect(formatMoney(value)).toBe('—')
      expect(formatPercent(value)).toBe('—')
      expect(formatCurrency(value)).toBe('—')
    }
  })

  it('still renders a real numeric zero as zero', () => {
    expect(formatMoney(0)).toBe('¥0.00')
    expect(formatPercent(0)).toBe('0.00%')
    expect(formatCurrency(0)).toBe('¥0.00')
  })

  it('does not turn unavailable data into a privacy mask', () => {
    expect(formatMoney(null, true)).toBe('—')
    expect(formatPercent(null, true)).toBe('—')
    expect(formatCurrency(null, true)).toBe('—')
  })

  it('masks only real values when privacy mode is enabled', () => {
    expect(formatMoney(12345, true)).toBe('¥ ***,***')
    expect(formatPercent(0.25, true)).toBe('***')
    expect(formatCurrency(12345, true)).toBe('¥ ***,***')
  })
})
