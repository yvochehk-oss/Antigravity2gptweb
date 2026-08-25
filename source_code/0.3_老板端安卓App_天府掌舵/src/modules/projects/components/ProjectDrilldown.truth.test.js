import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const componentPath = fileURLToPath(new URL('./ProjectDrilldown.vue', import.meta.url))
const source = readFileSync(componentPath, 'utf8')

describe('ProjectDrilldown truth rendering', () => {
  it('does not hardcode a four-flow 100% closure conclusion', () => {
    expect(source).not.toContain('100% 闭环')
    expect(source).toContain('evidenceChain')
    expect(source).toContain("label: '未核验'")
  })

  it('does not render a fabricated nominal amount when backend data is missing', () => {
    expect(source).toContain('名义金额暂无')
    expect(source).not.toContain('item.nominal || item.real')
  })
})
