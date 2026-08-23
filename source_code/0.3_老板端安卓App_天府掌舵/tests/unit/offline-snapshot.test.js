import { clearSnapshots, readSnapshot, sanitizeSnapshot, saveSnapshot, snapshotSavedAt } from '../../src/offline/snapshot'

describe('offline snapshots', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-22T08:30:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('round-trips JSON data with a saved timestamp', async () => {
    const cockpit = { revenue: 128_000_000, risks: ['invoice'] }
    await saveSnapshot('cockpit', cockpit)

    expect(await readSnapshot('cockpit', null)).toEqual(cockpit)
    expect(snapshotSavedAt('cockpit')).toBe('2026-08-22T08:30:00.000Z')
  })

  it('uses the fallback when a snapshot is absent or malformed', async () => {
    const fallback = { source: 'default' }

    expect(await readSnapshot('missing', fallback)).toBe(fallback)

    localStorage.setItem('cdjg_snapshot_broken', '{invalid json')
    expect(await readSnapshot('broken', fallback)).toBe(fallback)
    expect(snapshotSavedAt('broken')).toBeNull()
  })

  it('expires stale snapshots and clears all cached business data', async () => {
    await saveSnapshot('cockpit', { revenue: 1 })
    await saveSnapshot('projects', [{ id: 1 }])

    vi.advanceTimersByTime(24 * 60 * 60 * 1000 + 1)
    expect(await readSnapshot('cockpit', null)).toBeNull()

    clearSnapshots()
    expect(localStorage.getItem('cdjg_snapshot_projects')).toBeNull()
  })

  it('strips sensitive fields before persisting snapshots', async () => {
    const sensitive = {
      company_name: '天府建工第七分公司',
      uscc: '91510100MA5XYZ12345',
      legal_representative: '张三',
      registered_capital: 50_000_000,
      tax_no: '91510100123456789X',
      bank_account: '6222021234567890123',
      contract_amount: 12_800_000,
      nested: {
        invoice_no: 'INV-2026-001',
        invoice_amount: 128_000,
        legal_phone: '13800000000'
      }
    }
    await saveSnapshot('companies', sensitive)

    const stored = JSON.parse(localStorage.getItem('cdjg_snapshot_companies'))
    expect(stored.cipher).toBeTypeOf('string')
    expect(stored.cipher).not.toContain('91510100MA5XYZ12345')
    expect(stored.cipher).not.toContain('张三')
    expect(stored.cipher).not.toContain('INV-2026-001')

    const restored = await readSnapshot('companies', null)
    expect(restored.company_name).toBe('天府建工第七分公司')
    expect(restored.uscc).toBeUndefined()
    expect(restored.legal_representative).toBeUndefined()
    expect(restored.registered_capital).toBeUndefined()
    expect(restored.tax_no).toBeUndefined()
    expect(restored.bank_account).toBeUndefined()
    expect(restored.contract_amount).toBeUndefined()
    expect(restored.nested.invoice_no).toBeUndefined()
    expect(restored.nested.invoice_amount).toBeUndefined()
    expect(restored.nested.legal_phone).toBeUndefined()
  })

  it('returns a sanitized copy without mutating the source data', () => {
    const original = { name: '天府建工', uscc: '91510100MA5XYZ12345' }
    const sanitized = sanitizeSnapshot(original)

    expect(sanitized.name).toBe('天府建工')
    expect(sanitized.uscc).toBeUndefined()
    expect(original.uscc).toBe('91510100MA5XYZ12345')
  })
})