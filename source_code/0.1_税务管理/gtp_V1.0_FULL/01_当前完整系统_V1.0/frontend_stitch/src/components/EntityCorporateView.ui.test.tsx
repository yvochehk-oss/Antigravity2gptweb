import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LegalEntityOperatingProjection } from '../api';
import type { EntityTaxLedgerRecord } from '../types';
import { fetchEntityTaxLedger, fetchLegalEntityOperatingProjection } from '../api';
import { EntityCorporateView } from './EntityCorporateView';

vi.mock('../api', () => ({
  fetchEntityTaxLedger: vi.fn(),
  fetchLegalEntityOperatingProjection: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function projection(
  entityCode: string,
  period: string,
  revenue = 1000,
  status: 'READY' | 'DEGRADED' = 'READY',
): LegalEntityOperatingProjection {
  const contribution = {
    projectId: 1,
    projectCode: 'P-001',
    projectName: `${entityCode} 项目贡献`,
    revenue,
    bookCostProjection: 600,
    accountingProfitProjection: revenue - 600,
    outputVat: 90,
    inputVat: 36,
    deductibleInputVat: 30,
    nondeductibleInputVat: 4,
    pendingInputVat: 2,
    internalTradeNet: 120,
    internalTradeVat: 10.8,
    factCount: 2,
    factIds: [11, 12],
  };
  return {
    status,
    scope: 'LEGAL_ENTITY_PROJECTION',
    isFilingBasis: false,
    entityCode,
    period,
    revenue,
    bookCostProjection: 600,
    accountingProfitProjection: revenue - 600,
    outputVat: 90,
    inputVat: 36,
    deductibleInputVat: 30,
    nondeductibleInputVat: 4,
    pendingInputVat: 2,
    inputVatAccounted: 36,
    inputVatUnaccounted: 0,
    inputVatIdentityOk: true,
    internalTradeNet: 120,
    internalTradeVat: 10.8,
    projectContributions: [contribution],
    nonProjectContribution: {
      ...contribution,
      projectId: null,
      projectCode: '',
      projectName: '非项目归属',
      revenue: 0,
      bookCostProjection: 0,
      accountingProfitProjection: 0,
      outputVat: 0,
      inputVat: 0,
      deductibleInputVat: 0,
      nondeductibleInputVat: 0,
      pendingInputVat: 0,
      internalTradeNet: 0,
      internalTradeVat: 0,
      factCount: 0,
      factIds: [],
    },
    factCount: 2,
    factIds: [11, 12],
    dataGaps: status === 'DEGRADED' ? ['INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW'] : [],
    sourceOfTruth: 'analytics_canonical_facts_current',
    officialVatLedger: 'entity_vat_ledgers',
    limitations: [],
    calculationVersion: 'legal-entity-canonical-scope-v1',
  };
}

function statutoryRecord(entityCode: string, period: string, outputVat = 90): EntityTaxLedgerRecord {
  return {
    id: `${entityCode}-${period}`,
    period,
    entityId: 1,
    reportingPartyId: 1,
    entityCode,
    entityName: `${entityCode} 测试法人`,
    businessRole: '施工',
    legalEntity: true,
    scope: 'LEGAL_ENTITY_STATUTORY',
    isFilingBasis: true,
    sourceOfTruth: 'entity_vat_ledgers',
    openingInputCredit: 10,
    outputVat,
    inputVat: 54,
    taxPrepayment: 12,
    vatPayableBeforePrepayment: 26,
    closingInputCredit: 3,
    vatPayableAfterPrepayment: 14,
    unappliedTaxPrepayment: 0,
    calculationRunId: 101,
    runKind: 'NORMAL',
    runStatus: 'SUCCEEDED',
    rulesetVersion: 'v3',
    periodState: 'OPEN',
    inputSnapshotSha256: 'input-sha',
    resultSha256: 'result-sha',
    legalEntityVatIdentityOk: true,
    lineageComponents: [],
    dataStatus: 'READY',
    dataGaps: [],
    trusted: true,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchLegalEntityOperatingProjection).mockImplementation(async (entityCode, period = '') => (
    projection(entityCode, period, 1000)
  ));
  vi.mocked(fetchEntityTaxLedger).mockResolvedValue({
    status: 'READY',
    message: '',
    items: [],
  });
});

describe('EntityCorporateView legal-entity dual-scope workspace', () => {
  it('renders Projection and Statutory as distinct scopes and never invents CIT or after-tax profit', async () => {
    vi.mocked(fetchEntityTaxLedger).mockImplementation(async () => {
      const period = (screen.queryByLabelText('期间') as HTMLInputElement | null)?.value || '';
      return { status: 'READY', message: '', items: [statutoryRecord('A08', period)] };
    });

    render(<EntityCorporateView />);

    expect(await screen.findByText('LEGAL_ENTITY_PROJECTION · 管理/经营投影，非申报口径')).toBeInTheDocument();
    expect(screen.getByText('LEGAL_ENTITY_STATUTORY · 法人申报口径')).toBeInTheDocument();
    expect(screen.getByText('CIT / 税后利润：NOT AVAILABLE（等待确定性 CIT 引擎接入）')).toBeInTheDocument();
    expect(screen.getByText('项目穿透贡献 (Project Contributions)')).toBeInTheDocument();
    expect(await screen.findByText('A08 项目贡献')).toBeInTheDocument();
  });

  it('shows explicit DEGRADED notices without merging Projection into statutory VAT', async () => {
    vi.mocked(fetchLegalEntityOperatingProjection).mockImplementation(async (entityCode, period = '') => (
      projection(entityCode, period, 1000, 'DEGRADED')
    ));
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({
      status: 'DEGRADED',
      message: '正式 VAT 需要复核',
      items: [],
    });

    render(<EntityCorporateView />);

    await waitFor(() => expect(screen.getAllByText(/DEGRADED/).length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW/)).toBeInTheDocument();
    expect(screen.getByText(/正式 VAT 需要复核/)).toBeInTheDocument();
  });

  it('aborts stale Projection and Statutory requests on rapid legal-entity switching', async () => {
    const oldProjection = deferred<LegalEntityOperatingProjection>();
    const oldLedger = deferred<{ status: 'READY'; message: string; items: EntityTaxLedgerRecord[] }>();
    let oldProjectionSignal: AbortSignal | undefined;
    let oldLedgerSignal: AbortSignal | undefined;

    vi.mocked(fetchLegalEntityOperatingProjection)
      .mockImplementationOnce((_entityCode, _period, signal) => {
        oldProjectionSignal = signal;
        return oldProjection.promise;
      })
      .mockImplementationOnce(async (entityCode, period = '') => projection(entityCode, period, 2200));
    vi.mocked(fetchEntityTaxLedger)
      .mockImplementationOnce((signal) => {
        oldLedgerSignal = signal;
        return oldLedger.promise;
      })
      .mockImplementationOnce(async () => ({ status: 'READY', message: '', items: [] }));

    render(<EntityCorporateView />);
    await waitFor(() => {
      expect(oldProjectionSignal).toBeDefined();
      expect(oldLedgerSignal).toBeDefined();
    });

    fireEvent.change(screen.getByLabelText('法人主体'), { target: { value: 'B01' } });

    expect(oldProjectionSignal?.aborted).toBe(true);
    expect(oldLedgerSignal?.aborted).toBe(true);
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('B01', expect.any(String), expect.any(AbortSignal)));
    expect(await screen.findByText('B01 项目贡献')).toBeInTheDocument();

    await act(async () => {
      oldProjection.resolve(projection('A08', '2026-09', 9900));
      oldLedger.resolve({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-09', 999)] });
      await Promise.all([oldProjection.promise, oldLedger.promise]);
      await Promise.resolve();
    });

    expect(screen.queryByText('A08 项目贡献')).not.toBeInTheDocument();
    expect(screen.getByText('B01 项目贡献')).toBeInTheDocument();
  });

  it('aborts stale requests when the month period changes and keeps only the newest period', async () => {
    const oldProjection = deferred<LegalEntityOperatingProjection>();
    const oldLedger = deferred<{ status: 'READY'; message: string; items: EntityTaxLedgerRecord[] }>();
    let oldProjectionSignal: AbortSignal | undefined;
    let oldLedgerSignal: AbortSignal | undefined;

    vi.mocked(fetchLegalEntityOperatingProjection)
      .mockImplementationOnce((_entityCode, _period, signal) => {
        oldProjectionSignal = signal;
        return oldProjection.promise;
      })
      .mockImplementationOnce(async (entityCode, period = '') => projection(entityCode, period, 3300));
    vi.mocked(fetchEntityTaxLedger)
      .mockImplementationOnce((signal) => {
        oldLedgerSignal = signal;
        return oldLedger.promise;
      })
      .mockImplementationOnce(async () => ({
        status: 'READY',
        message: '',
        items: [statutoryRecord('A08', '2026-10', 109)],
      }));

    render(<EntityCorporateView />);
    await waitFor(() => expect(oldProjectionSignal).toBeDefined());

    fireEvent.change(screen.getByLabelText('期间'), { target: { value: '2026-10' } });

    expect(oldProjectionSignal?.aborted).toBe(true);
    expect(oldLedgerSignal?.aborted).toBe(true);
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('A08', '2026-10', expect.any(AbortSignal)));
    expect(await screen.findByText('A08 项目贡献')).toBeInTheDocument();
    expect(screen.getByText('A08 · 2026-10')).toBeInTheDocument();

    await act(async () => {
      oldProjection.resolve(projection('A08', '2026-09', 8800));
      oldLedger.resolve({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-09', 999)] });
      await Promise.all([oldProjection.promise, oldLedger.promise]);
      await Promise.resolve();
    });

    expect(screen.getByText('A08 · 2026-10')).toBeInTheDocument();
  });

  it('shows statutory VAT only for the selected legal entity and selected period', async () => {
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({
      status: 'READY',
      message: '',
      items: [
        statutoryRecord('A08', '2026-10', 109),
        statutoryRecord('B01', '2026-10', 999),
        statutoryRecord('A08', '2026-08', 888),
      ],
    });

    render(<EntityCorporateView />);
    fireEvent.change(screen.getByLabelText('期间'), { target: { value: '2026-10' } });

    await waitFor(() => expect(fetchEntityTaxLedger).toHaveBeenCalledTimes(2));
    expect(screen.getByText('¥109')).toBeInTheDocument();
    expect(screen.queryByText('¥999')).not.toBeInTheDocument();
    expect(screen.queryByText('¥888')).not.toBeInTheDocument();
  });
});
