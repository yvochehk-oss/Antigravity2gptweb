import { describe, expect, it } from 'vitest'
import { detectProjectMentions, buildMentionSegments } from '../../src/utils/mentions'

const PROJECTS = [
  { id: 1, project_code: 'CD-TF-001', name: '天府新区金融中心二期' },
  { id: 2, project_code: 'CY-CQ-002', name: '成渝双城经济圈跨江特大桥' },
  { id: 3, project_code: 'YB-DEMO-001', name: '宜宾示范工业项目' }
]

describe('detectProjectMentions', () => {
  it('returns [] when text is empty', () => {
    expect(detectProjectMentions('', PROJECTS)).toEqual([])
  })

  it('returns [] when projects list is empty', () => {
    expect(detectProjectMentions('any text', [])).toEqual([])
  })

  it('detects by project_code', () => {
    const chips = detectProjectMentions('详见 CD-TF-001 财务', PROJECTS)
    expect(chips).toHaveLength(1)
    expect(chips[0].shortCode).toBe('CD-TF-001')
    expect(chips[0].projectId).toBe(1)
  })

  it('detects by project name', () => {
    const chips = detectProjectMentions('当前天府新区金融中心二期进展顺利', PROJECTS)
    expect(chips).toHaveLength(1)
    expect(chips[0].code).toBe('CD-TF-001')
  })

  it('deduplicates when both code and name are present', () => {
    const chips = detectProjectMentions('天府新区金融中心二期 (CD-TF-001)', PROJECTS)
    expect(chips).toHaveLength(1)
  })

  it('handles multiple projects in one reply', () => {
    const chips = detectProjectMentions('对比 CD-TF-001 与 CY-CQ-002', PROJECTS)
    expect(chips.map(c => c.code).sort()).toEqual(['CD-TF-001', 'CY-CQ-002'])
  })

  it('returns [] when no match', () => {
    expect(detectProjectMentions('无相关项目', PROJECTS)).toEqual([])
  })
})

describe('buildMentionSegments', () => {
  it('returns a single text segment when no chips', () => {
    expect(buildMentionSegments('hello', [])).toEqual([{ type: 'text', value: 'hello' }])
  })

  it('splits text around a single chip', () => {
    const chips = [{ kind: 'project', code: 'CD-TF-001', label: '天府新区金融中心二期', projectId: 1, shortCode: 'CD-TF-001' }]
    const segments = buildMentionSegments('前 CD-TF-001 后', chips)
    expect(segments).toEqual([
      { type: 'text', value: '前 ' },
      { type: 'chip', value: 'CD-TF-001', chip: chips[0] },
      { type: 'text', value: ' 后' }
    ])
  })

  it('preserves order when multiple chips exist', () => {
    const a = { kind: 'project', code: 'CD-TF-001', label: '天府新区金融中心二期', projectId: 1, shortCode: 'CD-TF-001' }
    const b = { kind: 'project', code: 'CY-CQ-002', label: '成渝双城经济圈跨江特大桥', projectId: 2, shortCode: 'CY-CQ-002' }
    const segments = buildMentionSegments('先 CD-TF-001 然后 CY-CQ-002', [a, b])
    const chipCodes = segments.filter(s => s.type === 'chip').map(s => s.chip.code)
    expect(chipCodes).toEqual(['CD-TF-001', 'CY-CQ-002'])
  })

  it('falls back to a single text segment when no overlap survives', () => {
    const chips = [{ kind: 'project', code: 'XX-000', label: 'XX', projectId: 99, shortCode: 'XX-000' }]
    expect(buildMentionSegments('hello', chips)).toEqual([{ type: 'text', value: 'hello' }])
  })

  it('returns [] when text is empty', () => {
    expect(buildMentionSegments('', [])).toEqual([])
  })
})
