import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LegalEntityOperatingProjection } from '../api';
import type { EntityTaxLedgerRecord } from '../types';
import { fetchEntityTaxLedger, fetchLegalEntityOperatingProjection } from '../api';
import { fetchLegalEntities } from '../legalEntityApi';
import { EntityCorporateView } from './EntityCorporateView';

vi.mock('../api', () => ({
  fetchEntityTaxLedger: vi.fn(),
  fetchLegalEntityOperatingProjection: vi.fn(),
}));

vi.mock('../legalEntityApi', () => ({
  fetchLegalEntities: vi.fn(),
}));

function projection(entityCode: string, period: string, revenue = 1000): LegalEntityOperatingProjection {
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
    status: 'READY',
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
    dataGaps: [],
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
  vi.mocked(fetchLegalEntities).mockResolvedValue({
    status: 'READY',
    sourceOfTruth: 'parties+internal_entities',
    total: 2,
    items: [
      { partyId: 8, canonicalCode: 'A08', legalName: '四川锐宝建设工程有限公司' },
      { partyId: 31, canonicalCode: 'B01', legalName: '四川乾润和贸易有限公司' },
    ],
  });
  vi.mocked(fetchLegalEntityOperatingProjection).mockImplementation(async (entityCode, period = '') => projection(entityCode, period));
  vi.mocked(fetchEntityTaxLedger).mockResolvedValue({ status: 'READY', message: '', items: [] });
});

describe('EntityCorporateView legal-entity dual-scope workspace', () => {
  it('T2 loads legal entity names from Party SSOT and drives downstream queries from the selected canonical code', async () => {
    render(<EntityCorporateView />);

    expect(fetchLegalEntities).toHaveBeenCalledTimes(1);
    const entitySelect = await screen.findByLabelText('法人主体');
    await waitFor(() => expect(entitySelect).toHaveValue('A08'));
    expect(entitySelect.querySelectorAll('option')).toHaveLength(2);
    expect(screen.getByRole('option', { name: 'A08 · 四川锐宝建设工程有限公司' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'B01 · 四川乾润和贸易有限公司' })).toBeInTheDocument();

    fireEvent.change(entitySelect, { target: { value: 'B01' } });
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('B01', expect.any(String), expect.any(AbortSignal)));
    expect(screen.getByText(/【B01 · 四川乾润和贸易有限公司】/)).toBeInTheDocument();
    expect(await screen.findByText('B01 项目贡献')).toBeInTheDocument();
  });

  it('keeps Projection and Statutory as distinct scopes and never invents CIT or after-tax profit', async () => {
    render(<EntityCorporateView />);
    expect(await screen.findByText('LEGAL_ENTITY_PROJECTION · 管理/经营投影，非申报口径')).toBeInTheDocument();
    expect(screen.getByText('LEGAL_ENTITY_STATUTORY · 法人申报口径')).toBeInTheDocument();
    expect(screen.getByText('CIT / 税后利润：NOT AVAILABLE（等待确定性 CIT 引擎接入）')).toBeInTheDocument();
    expect(screen.getByText('项目穿透贡献 (Project Contributions)')).toBeInTheDocument();
  });

  it('keeps the selected period synchronized with year/month selectors', async () => {
    render(<EntityCorporateView />);
    await waitFor(() => expect(screen.getByLabelText('法人主体')).toHaveValue('A08'));
    fireEvent.change(screen.getByLabelText('所属年份'), { target: { value: '2027' } });
    fireEvent.change(screen.getByLabelText('所属月份'), { target: { value: '02' } });
    await waitFor(() => expect(fetchLegalEntityOperatingProjection).toHaveBeenLastCalledWith('A08', '2027-02', expect.any(AbortSignal)));
    expect(screen.getByLabelText('所属年份')).toHaveValue('2027');
    expect(screen.getByLabelText('所属月份')).toHaveValue('02');
  });

  it('shows statutory VAT only for the selected legal entity and period', async () => {
    vi.mocked(fetchEntityTaxLedger).mockResolvedValue({
      status: 'READY',
      message: '',
      items: [statutoryRecord('A08', '2026-10', 109), statutoryRecord('B01', '2026-10', 999)],
    });
    render(<EntityCorporateView />);
    await waitFor(() => expect(screen.getByLabelText('法人主体')).toHaveValue('A08'));
    fireEvent.change(screen.getByLabelText('期间'), { target: { value: '2026-10' } });
    await waitFor(() => expect(screen.getByText('¥109')).toBeInTheDocument());
    expect(screen.queryByText('¥999')).not.toBeInTheDocument();
  });

  it('displays fail-closed formal VAT empty guidance and never leaks Projection VAT figures', async () => {
    render(<EntityCorporateView />);
    expect(await screen.findByText(/上方【正式 VAT】是纳税申报口径/)).toBeInTheDocument();
    const statutorySection = screen.getByRole('region', { name: '正式 VAT' });
    expect(within(statutorySection).getAllByText('—')).toHaveLength(6);
    expect(statutorySection).not.toHaveTextContent('¥90');
    expect(statutorySection).not.toHaveTextContent('¥36');
  });
});
