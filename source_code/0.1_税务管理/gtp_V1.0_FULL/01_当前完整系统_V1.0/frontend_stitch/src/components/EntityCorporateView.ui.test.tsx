import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LegalEntityOperatingProjection } from '../api';
import type { EntityTaxLedgerRecord } from '../types';
import { fetchLegalEntityOperatingProjection } from '../api';
import { fetchLegalEntities, fetchLegalEntityFactPeriods, fetchLegalEntityStatutoryVat } from '../legalEntityApi';
import { EntityCorporateView } from './EntityCorporateView';

vi.mock('../api', () => ({ fetchLegalEntityOperatingProjection: vi.fn() }));
vi.mock('../legalEntityApi', () => ({ fetchLegalEntities: vi.fn(), fetchLegalEntityFactPeriods: vi.fn(), fetchLegalEntityStatutoryVat: vi.fn() }));

const fetchEntityTaxLedger = fetchLegalEntityStatutoryVat as unknown as (signal?: AbortSignal) => Promise<{ status: 'READY' | 'DEGRADED'; message: string; items: EntityTaxLedgerRecord[] }>;

const LEGAL_ENTITY_FIXTURES = [
  ['A08', '四川锐宝建设工程有限公司'], ['A01', '中镌（湖北）建筑有限公司'], ['A02', '四川中恒腾鸣建筑工程有限公司'], ['A03', '四川屹明汇建设工程有限公司'], ['A05', '四川帆亿通信科技有限公司'], ['A06', '四川裕合荣建筑工程有限公司'], ['A07', '四川铁安电力工程有限公司'], ['A09', '四川顺程源建筑工程有限公司'], ['A10', '四川鼎新源建筑工程有限公司'], ['A11', '成都巨邦建设工程有限公司'], ['B01', '四川乾润和贸易有限公司'], ['B02', '四川兴誉诚商贸有限公司'], ['B03', '四川坤珀贸易有限公司'], ['B04', '四川矗佳商贸有限公司'], ['B05', '广元玖硕商贸有限公司'], ['B06', '广州采云广告有限公司'], ['B07', '成都恒创嘉泰贸易有限公司'], ['B08', '成都鑫晨鼎升商贸有限公司'], ['B09', '格尔木青泽贸易有限公司'], ['B10', '重庆朗德乾润商贸有限公司'], ['C01', '四川本盛劳务有限公司'], ['C02', '四川灏琅建筑劳务有限公司'], ['D01', '四川乾润和机械设备租赁有限公司'], ['D02', '四川乾诺机械租赁有限公司'], ['D03', '四川惠润农业设备有限公司'],
] as const;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function projection(entityCode: string, period: string, revenue = 1000, status: 'READY' | 'DEGRADED' = 'READY'): LegalEntityOperatingProjection {
  const contribution = { projectId: 1, projectCode: 'P-001', projectName: `${entityCode} 项目贡献`, revenue, bookCostProjection: 600, accountingProfitProjection: revenue - 600, outputVat: 90, inputVat: 36, deductibleInputVat: 30, nondeductibleInputVat: 4, pendingInputVat: 2, internalTradeNet: 120, internalTradeVat: 10.8, factCount: 2, factIds: [11, 12] };
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
    nonProjectContribution: { ...contribution, projectId: null, projectCode: '', projectName: '非项目归属', revenue: 0, bookCostProjection: 0, accountingProfitProjection: 0, outputVat: 0, inputVat: 0, deductibleInputVat: 0, nondeductibleInputVat: 0, pendingInputVat: 0, internalTradeNet: 0, internalTradeVat: 0, factCount: 0, factIds: [] },
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
  return { id: `${entityCode}-${period}`, period, entityId: 1, reportingPartyId: 1, entityCode, entityName: `${entityCode} 测试法人`, businessRole: '施工', legalEntity: true, scope: 'LEGAL_ENTITY_STATUTORY', isFilingBasis: true, sourceOfTruth: 'entity_vat_ledgers', openingInputCredit: 10, outputVat, inputVat: 54, taxPrepayment: 12, vatPayableBeforePrepayment: 26, closingInputCredit: 3, vatPayableAfterPrepayment: 14, unappliedTaxPrepayment: 0, calculationRunId: 101, runKind: 'NORMAL', runStatus: 'SUCCEEDED', rulesetVersion: 'v3', periodState: 'OPEN', inputSnapshotSha256: 'input-sha', resultSha256: 'result-sha', legalEntityVatIdentityOk: true, lineageComponents: [], dataStatus: 'READY', dataGaps: [], trusted: true };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchLegalEntities).mockResolvedValue({ status: 'READY', sourceOfTruth: 'parties+internal_entities', items: LEGAL_ENTITY_FIXTURES.map(([canonicalCode, legalName], index) => ({ partyId: index + 1, canonicalCode, legalName })), total: LEGAL_ENTITY_FIXTURES.length });
  vi.mocked(fetchLegalEntityFactPeriods).mockImplementation(async entityCode => ({ status: 'READY', entityCode, sourceOfTruth: 'analytics_canonical_facts_current', factType: 'invoice', totalFactCount: 24, unperiodizedFactCount: 0, periods: [{ period: '2026-03', factCount: 12, isPrimary: true }, { period: '2025-08', factCount: 6, isPrimary: false }, { period: '2024-07', factCount: 4, isPrimary: false }, { period: '2023-07', factCount: 2, isPrimary: false }] }));
  vi.mocked(fetchLegalEntityOperatingProjection).mockImplementation(async (entityCode, period = '') => projection(entityCode, period, 1000));
  vi.mocked(fetchEntityTaxLedger).mockResolvedValue({ status: 'READY', message: '', items: [] });
});

describe('EntityCorporateView legal-entity dual-scope workspace', () => {
  it('renders management projection and statutory VAT as distinct Chinese business scopes and never invents income tax or after-tax profit', async () => {
    render(<EntityCorporateView />);
    expect(await screen.findByText('法人管理／经营投影 · 管理口径，非申报口径')).toBeInTheDocument();
    expect(screen.getByText('法人法定申报 · 法人申报口径')).toBeInTheDocument();
    expect(screen.getByText('企业所得税／税后利润：暂未接入（等待确定性企业所得税引擎接入）')).toBeInTheDocument();
    expect(screen.getByText('项目穿透贡献')).toBeInTheDocument();
    expect(await screen.findByText('A08 项目贡献')).toBeInTheDocument();
  });

  it('keeps the title independent from entity and period controls', async () => {
    const { container } = render(<EntityCorporateView />);
    await waitFor(() => expect(screen.getByLabelText('法人主体')).toHaveValue('A08'));
    const title = container.querySelector('[data-page-title="entity-profile"]');
    const controls = container.querySelector('[data-page-controls="entity-profile"]');
    expect(title).toBeInTheDocument();
    expect(controls).toBeInTheDocument();
    expect(title?.parentElement).toBe(controls?.parentElement);
    expect(title).not.toContainElement(controls);
  });

  it('shows explicit Chinese degraded notices without merging projection into statutory VAT', async () => {
    vi.mocked(fetchLegalEntityOperatingProjection).mockImplementation(async (entityCode, period = '') => projection(entityCode, period, 1000, 'DEGRADED'));
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({ status: 'DEGRADED', message: '法定申报增值税需要复核', items: [] });
    render(<EntityCorporateView />);
    await waitFor(() => expect(screen.getAllByText(/降级运行/).length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText(/进项税额合规性待确认/)).toBeInTheDocument();
    expect(screen.getByText(/法定申报增值税需要复核/)).toBeInTheDocument();
    expect(screen.queryByText(/INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW/)).not.toBeInTheDocument();
  });

  it('aborts stale Projection and Statutory requests on rapid legal-entity switching', async () => {
    const oldProjection = deferred<LegalEntityOperatingProjection>();
    const oldLedger = deferred<{ status: 'READY'; message: string; items: EntityTaxLedgerRecord[] }>();
    let oldProjectionSignal: AbortSignal | undefined;
    let oldLedgerSignal: AbortSignal | undefined;
    vi.mocked(fetchLegalEntityOperatingProjection).mockImplementationOnce((_entityCode, _period, signal) => { oldProjectionSignal = signal; return oldProjection.promise; }).mockImplementationOnce(async (entityCode, period = '') => projection(entityCode, period, 2200));
    vi.mocked(fetchEntityTaxLedger).mockImplementationOnce(signal => { oldLedgerSignal = signal; return oldLedger.promise; }).mockImplementationOnce(async () => ({ status: 'READY', message: '', items: [] }));

    render(<EntityCorporateView />);
    await waitFor(() => { expect(oldProjectionSignal).toBeDefined(); expect(oldLedgerSignal).toBeDefined(); expect(screen.getByLabelText('法人主体')).toHaveValue('A08'); });
    fireEvent.change(screen.getByLabelText('法人主体'), { target: { value: 'B01' } });
    expect(oldProjectionSignal?.aborted).toBe(true);
    expect(oldLedgerSignal?.aborted).toBe(true);
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('B01', expect.any(String), expect.any(AbortSignal)));
    expect(await screen.findByText('B01 项目贡献')).toBeInTheDocument();
    await act(async () => { oldProjection.resolve(projection('A08', '2026-09', 9900)); oldLedger.resolve({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-09', 999)] }); await Promise.all([oldProjection.promise, oldLedger.promise]); await Promise.resolve(); });
    expect(screen.queryByText('A08 项目贡献')).not.toBeInTheDocument();
    expect(screen.getByText('B01 项目贡献')).toBeInTheDocument();
  });

  it('aborts stale requests when the month period changes and keeps only the newest period', async () => {
    const oldProjection = deferred<LegalEntityOperatingProjection>();
    const oldLedger = deferred<{ status: 'READY'; message: string; items: EntityTaxLedgerRecord[] }>();
    let oldProjectionSignal: AbortSignal | undefined;
    let oldLedgerSignal: AbortSignal | undefined;
    vi.mocked(fetchLegalEntityOperatingProjection).mockImplementationOnce((_entityCode, _period, signal) => { oldProjectionSignal = signal; return oldProjection.promise; }).mockImplementationOnce(async (entityCode, period = '') => projection(entityCode, period, 3300));
    vi.mocked(fetchEntityTaxLedger).mockImplementationOnce(signal => { oldLedgerSignal = signal; return oldLedger.promise; }).mockImplementationOnce(async () => ({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-03', 109)] }));

    render(<EntityCorporateView />);
    await waitFor(() => expect(oldProjectionSignal).toBeDefined());
    fireEvent.click(await screen.findByRole('button', { name: '2026年03月 · 12笔（主力数据）' }));
    expect(oldProjectionSignal?.aborted).toBe(true);
    expect(oldLedgerSignal?.aborted).toBe(true);
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('A08', '2026-03', expect.any(AbortSignal)));
    expect(await screen.findByText('A08 项目贡献')).toBeInTheDocument();
    expect(screen.getByText('A08 · 2026-03')).toBeInTheDocument();
    await act(async () => { oldProjection.resolve(projection('A08', '2026-09', 8800)); oldLedger.resolve({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-09', 999)] }); await Promise.all([oldProjection.promise, oldLedger.promise]); await Promise.resolve(); });
    expect(screen.getByText('A08 · 2026-03')).toBeInTheDocument();
  });

  it('shows statutory VAT only for the selected legal entity and selected period', async () => {
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({ status: 'READY', message: '', items: [statutoryRecord('A08', '2026-03', 109), statutoryRecord('B01', '2026-03', 999), statutoryRecord('A08', '2026-08', 888)] });
    render(<EntityCorporateView />);
    fireEvent.click(await screen.findByRole('button', { name: '2026年03月 · 12笔（主力数据）' }));
    await waitFor(() => expect(fetchEntityTaxLedger).toHaveBeenCalledTimes(2));
    expect(screen.getByText('¥109')).toBeInTheDocument();
    expect(screen.queryByText('¥999')).not.toBeInTheDocument();
    expect(screen.queryByText('¥888')).not.toBeInTheDocument();
  });

  it('T_UI_1: renders complete 25 legal entity names and synchronizes title, badge and API query upon selection', async () => {
    render(<EntityCorporateView />);
    const entitySelect = screen.getByLabelText('法人主体');
    await waitFor(() => expect(entitySelect).toHaveValue('A08'));
    expect(entitySelect.querySelectorAll('option')).toHaveLength(25);
    expect(screen.getByRole('option', { name: 'A08 · 四川锐宝建设工程有限公司' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'B01 · 四川乾润和贸易有限公司' })).toBeInTheDocument();
    fireEvent.change(entitySelect, { target: { value: 'B01' } });
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('B01', expect.any(String), expect.any(AbortSignal)));
    expect(screen.getByText(/【B01 · 四川乾润和贸易有限公司】/)).toBeInTheDocument();
    expect(screen.getByText(/（四川乾润和贸易有限公司）/)).toBeInTheDocument();
  });

  it('T_UI_2: changes visible year and month selects independently without state drift', async () => {
    render(<EntityCorporateView />);
    await waitFor(() => expect(screen.getByLabelText('法人主体')).toHaveValue('A08'));
    fireEvent.change(screen.getByLabelText('所属年份'), { target: { value: '2027' } });
    fireEvent.change(screen.getByLabelText('所属月份'), { target: { value: '02' } });
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('A08', '2027-02', expect.any(AbortSignal)));
    expect(screen.getByLabelText('所属年份')).toHaveValue('2027');
    expect(screen.getByLabelText('所属月份')).toHaveValue('02');
  });

  it('T_UI_3: renders API-driven fact-period recommendations and synchronizes visible period controls on click', async () => {
    render(<EntityCorporateView />);
    const quickBtn = await screen.findByRole('button', { name: '2026年03月 · 12笔（主力数据）' });
    expect(fetchLegalEntityFactPeriods).toHaveBeenCalledWith('A08', expect.any(AbortSignal));
    expect(screen.getByRole('button', { name: '2025年08月 · 6笔' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2024年07月 · 4笔' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2023年07月 · 2笔' })).toBeInTheDocument();
    fireEvent.click(quickBtn);
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('A08', '2026-03', expect.any(AbortSignal)));
    expect(screen.getByLabelText('所属年份')).toHaveValue('2026');
    expect(screen.getByLabelText('所属月份')).toHaveValue('03');
  });

  it('T_UI_4: displays fail-closed formal VAT empty guidance note and never falls back to Projection figures', async () => {
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({ status: 'READY', message: '', items: [] });
    render(<EntityCorporateView />);
    expect(await screen.findByText('法人管理／经营投影 · 管理口径，非申报口径')).toBeInTheDocument();
    expect(await screen.findByText(/上方【法定申报增值税】是纳税申报口径/)).toBeInTheDocument();
    expect(screen.getByText(/当前期间未归档正式台账，故显示为“—”/)).toBeInTheDocument();
    const statutorySection = screen.getByRole('region', { name: '法定申报增值税' });
    expect(within(statutorySection).getAllByText('—')).toHaveLength(6);
    expect(statutorySection).not.toHaveTextContent('¥90');
    expect(statutorySection).not.toHaveTextContent('¥36');
  });
});
