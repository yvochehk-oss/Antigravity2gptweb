/**
 * Formats a numeric amount as Chinese currency for UI display.
 *
 * @param {number|string|null|undefined} value Raw amount (server returns 元, not 100元/万元).
 * @param {boolean} [hidden=false] When true, returns a masked placeholder suitable
 *   for "private mode" toggles (privacy mode in the executive header).
 * @returns {string} Display string, in the following patterns:
 *   - `hidden=true`                → `"¥ ***,***"`
 *   - `null`/`undefined`/`NaN`     → `"¥ 0"`
 *   - `|value| >= 100_000_000`     → `"¥1.23 亿"` (two decimals, 亿 unit)
 *   - `|value| >= 10_000`          → `"¥1.23 万"` (two decimals, 万 unit)
 *   - otherwise                    → locale-formatted `"¥1,234.56"` (two decimals)
 *
 * @example
 *   formatMoney(100_000_000)    // "¥1.00 亿"
 *   formatMoney(150_000)        // "¥15.00 万"
 *   formatMoney(5_000)          // "¥5,000.00"
 *   formatMoney(null)           // "¥ 0"
 *   formatMoney(12345, true)    // "¥ ***,***"
 */
export function formatMoney(value, hidden = false) {
  if (hidden) return '¥ ***,***'
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '¥ 0'
  const amount = Number(value)
  const abs = Math.abs(amount)
  if (abs >= 100_000_000) return `¥${(amount / 100_000_000).toFixed(2)} 亿`
  if (abs >= 10_000) return `¥${(amount / 10_000).toFixed(2)} 万`
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * Formats a ratio or a percentage value into a two-decimal percent string.
 *
 * Accepts either a fraction (0.1234 → "12.34%") or a pre-multiplied percentage
 * (12.34 → "12.34%"). Heuristically uses an absolute-value > 1 threshold to
 * distinguish the two; this matches the convention used throughout the
 * dashboard where percentages like "12.34%" are returned as bare numbers.
 *
 * @param {number|string|null|undefined} value Fraction (0..1) or percentage value.
 * @param {boolean} [hidden=false] When true, returns `"***"` for privacy mode.
 * @returns {string} Always two decimals followed by `"%"`.
 *
 * @example
 *   formatPercent(0.1234)   // "12.34%"
 *   formatPercent(12.34)    // "12.34%"
 *   formatPercent(null)     // "0.00%"
 */
export function formatPercent(value, hidden = false) {
  if (hidden) return '***'
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '0.00%'
  const ratio = Number(value)
  const asFraction = Math.abs(ratio) > 1 ? ratio / 100 : ratio
  return `${(asFraction * 100).toFixed(2)}%`
}

/**
 * Formats a numeric amount as a currency string with a configurable symbol.
 *
 * Unlike {@link formatMoney}, this function does NOT switch units (亿/万) —
 * it always renders the amount in two-decimal locale format. Use this when the
 * component layout already constrains the space (tables, dense KPI rows) or
 * when a non-yuan currency is needed.
 *
 * @param {number|string|null|undefined} value Raw amount.
 * @param {boolean} [hidden=false] When true, returns `"<symbol> ***,***"`.
 * @param {{ symbol?: string }} [options] Display options.
 * @param {string} [options.symbol="¥"] Currency symbol prepended to the value.
 * @returns {string} Locale-formatted currency string with two decimals.
 *
 * @example
 *   formatCurrency(12_345.6)               // "¥12,345.60"
 *   formatCurrency(7_890, false, { symbol: '$' })  // "$7,890.00"
 *   formatCurrency(7_890, true)            // "¥ ***,***"
 */
export function formatCurrency(value, hidden = false, { symbol = '¥' } = {}) {
  if (hidden) return `${symbol} ***,***`
  if (value === undefined || value === null || Number.isNaN(Number(value))) return `${symbol} 0.00`
  return `${symbol}${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
