import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaxLedgerView } from './TaxLedgerView';
import type { EntityTaxLedgerRecord } from '../types';

const record: EntityTaxLedgerRecord = {
  id: 'A01-2026-08',
  period: '2026-08',
  entityId: 1,
  reportingPartyId: 1,
  entityCode: 'A01',
  entityName: '成都建工测试法人',
  businessRole: '施工',
  legalEntity: true,
  scope: 'LEGAL_ENTITY_STATUTORY',
  isFilingBasis: true,
  sourceOfTruth: 'entity_vat_ledgers',
  openingInputCredit: 10,
  outputVat: 90,
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

const defaultProps = {
  dataStatus: 'READY' as const,
  dataStatusMessage: '',
  onRetry: vi.fn(),
  onOpenNewRecordModal: vi.fn(),
  onOpenExportModal: vi.fn(),
  onAskAiAboutRisk: vi.fn(),
  onRebuildTaxLedger: vi.fn(async () => undefined),
  isRebuilding: false,
};

describe('TaxLedgerView statutory presentation', () => {
  it('renders only statutory VAT KPIs and table columns using Chinese business labels', () => {
    render(<TaxLedgerView {...defaultProps} records={[record]} />);

    expect(screen.getByText('法人法定申报口径')).toBeInTheDocument();
    for (const label of [
      '期初留抵',
      '销项税额',
      '进项税额',
      '税款预缴',
      '预缴前应纳税额',
      '实际应纳增值税',
      '期末留抵',
      '未抵完预缴',
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }

    for (const column of ['法人', '所属期', '期初留抵', '销项税额', '进项税额', '税款预缴', '应纳增值税', '期末留抵', '期间状态', '计算运行状态']) {
      expect(screen.getByRole('columnheader', { name: column })).toBeInTheDocument();
    }

    expect(screen.getByText('开放期')).toBeInTheDocument();
    expect(screen.getByText('执行成功')).toBeInTheDocument();
    expect(screen.queryByText('OPEN')).not.toBeInTheDocument();
    expect(screen.queryByText('SUCCEEDED')).not.toBeInTheDocument();
    expect(screen.getAllByText('¥14').length).toBeGreaterThan(0);
  });

  it('keeps the page title independent from export controls', () => {
    const { container } = render(<TaxLedgerView {...defaultProps} records={[record]} />);
    const title = container.querySelector('[data-page-title="tax-ledger"]');
    const controls = container.querySelector('[data-page-controls="tax-ledger"]');
    expect(title).toBeInTheDocument();
    expect(controls).toBeInTheDocument();
    expect(title?.parentElement).toBe(controls?.parentElement);
    expect(title).not.toContainElement(controls);
  });

  it('does not render legacy P&L KPI labels for an empty statutory ledger', () => {
    render(<TaxLedgerView {...defaultProps} records={[]} />);

    expect(screen.getByText(/接口正常、指定期间暂无已生成台账/)).toBeInTheDocument();
    expect(screen.queryByText('营业/计税收入')).not.toBeInTheDocument();
    expect(screen.queryByText('真实成本合计')).not.toBeInTheDocument();
    expect(screen.queryByText('预计利润')).not.toBeInTheDocument();
    expect(screen.queryByText('预计所得税')).not.toBeInTheDocument();
  });
});
