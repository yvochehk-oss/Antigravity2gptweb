import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EntityVatLineageDrawer } from './EntityVatLineageDrawer';
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
  lineageComponents: [{
    componentType: 'OUTPUT_VAT',
    amount: 90,
    outputVatEventId: 1101,
    inputVatClaimId: null,
    taxPrepaymentFactId: 1201,
    priorLedgerId: 1301,
    openingBalanceSeedId: null,
    invoiceFactId: 1401,
    sourceDocumentId: 1501,
  }],
  dataStatus: 'READY',
  dataGaps: [],
  trusted: true,
};

const defaultTaxLedgerProps = {
  dataStatus: 'READY' as const,
  dataStatusMessage: '',
  onRetry: vi.fn(),
  onOpenNewRecordModal: vi.fn(),
  onOpenExportModal: vi.fn(),
  onAskAiAboutRisk: vi.fn(),
  onRebuildTaxLedger: vi.fn(async () => undefined),
  isRebuilding: false,
};

function expectFieldValue(field: string, value: string): void {
  const fieldLabel = screen.getByText(field);
  expect(fieldLabel.parentElement).toHaveTextContent(value);
}

describe('EntityVatLineageDrawer', () => {
  it('opens from TaxLedgerView and closes through the drawer control', () => {
    render(<TaxLedgerView {...defaultTaxLedgerProps} records={[record]} />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '血缘溯源' }));

    expect(screen.getByRole('dialog', { name: '法定 VAT 血缘溯源' })).toBeInTheDocument();
    expect(screen.getByText(/成都建工测试法人 · 2026-08 · LEGAL_ENTITY_STATUTORY/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '关闭血缘溯源' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows a friendly empty state when the ledger row has no lineage components', () => {
    render(
      <EntityVatLineageDrawer
        open
        record={{ ...record, lineageComponents: [] }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog', { name: '法定 VAT 血缘溯源' })).toBeInTheDocument();
    expect(screen.getByText('当前法定 VAT 台账行暂无结构化血缘组件。')).toBeInTheDocument();
  });

  it('renders every typed lineage ID and uses a dash for null values', () => {
    render(<EntityVatLineageDrawer open record={record} onClose={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'OUTPUT_VAT' })).toBeInTheDocument();
    expectFieldValue('componentType', 'OUTPUT_VAT');
    expectFieldValue('amount', '90');
    expectFieldValue('outputVatEventId', '1101');
    expectFieldValue('inputVatClaimId', '—');
    expectFieldValue('taxPrepaymentFactId', '1201');
    expectFieldValue('priorLedgerId', '1301');
    expectFieldValue('openingBalanceSeedId', '—');
    expectFieldValue('invoiceFactId', '1401');
    expectFieldValue('sourceDocumentId', '1501');
  });

  it('does not expose invented lineage fields', () => {
    render(<EntityVatLineageDrawer open record={record} onClose={vi.fn()} />);

    expect(screen.queryByText('canonicalFactId')).not.toBeInTheDocument();
    expect(screen.queryByText('confidence')).not.toBeInTheDocument();
    expect(screen.queryByText('evidenceCount')).not.toBeInTheDocument();
    expect(screen.queryByText('sourceRefs')).not.toBeInTheDocument();
  });
});
