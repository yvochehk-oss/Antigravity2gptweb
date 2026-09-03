import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectDetailView } from './ProjectDetailView';
import type { ProjectItem, ProjectTaxAnalysisRecord } from '../types';
import { fetchProjectCounterparties, fetchProjectTaxAnalysis } from '../api';

vi.mock('../api', () => ({
  fetchProjectCounterparties: vi.fn(),
  fetchProjectTaxAnalysis: vi.fn(),
  deleteProjectData: vi.fn(),
}));

const project: ProjectItem = {
  id: '15',
  numericId: 15,
  projectCode: 'PRJ-15',
  name: '边界语义测试项目',
  constructionStage: '施工中',
  healthGrade: '未知',
  totalBudget: 1000000,
  spentAmount: 0,
  remainingBudget: 1000000,
  progressPercent: 10,
  taxRiskGrade: '未知',
  isOverBudget: null,
  managerName: '—',
  location: '成都',
  teamAvatars: [],
  costItems: [],
};

const baseAnalysis: ProjectTaxAnalysisRecord = {
  projectId: 15,
  projectCode: 'PRJ-15',
  projectName: '边界语义测试项目',
  period: '2026-08',
  entityCode: 'A01',
  scope: 'PROJECT_BOUNDARY',
  isFilingBasis: false,
  outInvoiceNet: 1000,
  outInvoiceVat: 90,
  inInvoiceNet: 600,
  inInvoiceVat: 70,
  deductibleInputVat: 54,
  nondeductibleInputVat: 6,
  pendingInputVat: 10,
  signedVatPosition: 36,
  internalEliminatedNet: 0,
  internalEliminatedVat: 0,
  inputVatAccounted: 60,
  inputVatUnaccounted: 10,
  inputVatIdentityOk: true,
  realCost: 600,
  invoiceCount: 5,
  sourceOfTruth: 'analytics_canonical_facts_current',
  legacyTablesUsed: false,
  realCostBasis: 'PAYMENT_CONFIRMED',
  dataGaps: [],
};

const defaultProps = {
  project,
  onBack: vi.fn(),
  onOpenNewRecordModal: vi.fn(),
  onOpenExportModal: vi.fn(),
  onAskAiAboutRisk: vi.fn(),
};

function mockAnalysis(item: ProjectTaxAnalysisRecord) {
  vi.mocked(fetchProjectTaxAnalysis).mockResolvedValueOnce({ status: 'READY', message: '', item });
  vi.mocked(fetchProjectCounterparties).mockResolvedValueOnce({ status: 'EMPTY', message: '', items: [], total: 0 });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProjectDetailView project-boundary semantics', () => {
  it('shows the complete input tax split and identity status from backend facts', async () => {
    mockAnalysis(baseAnalysis);
    render(<ProjectDetailView {...defaultProps} />);

    expect(await screen.findByText('项目管理边界 · 项目管理口径 · 非申报依据')).toBeInTheDocument();
    expect(screen.getByText('已确认可抵扣进项税额')).toBeInTheDocument();
    expect(screen.getByText('待认证／待判定进项税额')).toBeInTheDocument();
    expect(screen.getByText('不可抵扣进项税额')).toBeInTheDocument();
    expect(screen.getByText('已入账进项税额')).toBeInTheDocument();
    expect(screen.getByText('未入账进项税额')).toBeInTheDocument();
    expect(screen.getByText('进项一致性核验：通过')).toBeInTheDocument();
    expect(screen.getByTestId('pending-input-vat')).toHaveTextContent('10.00');
    expect(screen.getByTestId('nondeductible-input-vat')).toHaveTextContent('6.00');
  });

  it('labels positive and negative signed management positions without recomputing an amount', async () => {
    mockAnalysis({ ...baseAnalysis, signedVatPosition: 36 });
    const { unmount } = render(<ProjectDetailView {...defaultProps} />);
    expect(await screen.findByText('净销项增值税管理头寸')).toBeInTheDocument();
    expect(screen.getByTestId('signed-vat-position')).toHaveTextContent('36.00');
    unmount();

    mockAnalysis({ ...baseAnalysis, projectId: 16, signedVatPosition: -18 });
    render(<ProjectDetailView {...defaultProps} project={{ ...project, id: '16', numericId: 16 }} />);
    expect(await screen.findByText('净进项增值税管理头寸')).toBeInTheDocument();
    expect(screen.getByTestId('signed-vat-position')).toHaveTextContent('-18.00');
    expect(screen.queryByText('应纳增值税')).not.toBeInTheDocument();
    expect(screen.queryByText('申报税额')).not.toBeInTheDocument();
  });

  it('always exposes internal elimination for zero and non-zero backend values', async () => {
    mockAnalysis(baseAnalysis);
    const { unmount } = render(<ProjectDetailView {...defaultProps} />);
    expect(await screen.findByText('内部交易抵消')).toBeInTheDocument();
    expect(screen.getByText('本项目无内部交易抵消')).toBeInTheDocument();
    unmount();

    mockAnalysis({ ...baseAnalysis, projectId: 16, internalEliminatedNet: 120, internalEliminatedVat: 10.8 });
    render(<ProjectDetailView {...defaultProps} project={{ ...project, id: '16', numericId: 16 }} />);
    expect(await screen.findByTestId('internal-elimination-card')).toHaveTextContent('抵消净额');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('120.00');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('抵消增值税');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('10.80');
  });

  it('shows explicit identity failure and degraded cost state without fallback values', async () => {
    mockAnalysis({ ...baseAnalysis, inputVatIdentityOk: false, realCost: 0, realCostBasis: '', dataGaps: ['REAL_COST_NOT_AVAILABLE'] });
    render(<ProjectDetailView {...defaultProps} />);

    expect(await screen.findByText('进项一致性核验：未通过')).toBeInTheDocument();
    expect(screen.getAllByText('降级运行 · 实际成本尚未归集入账').length).toBeGreaterThan(0);
    await waitFor(() => expect(fetchProjectTaxAnalysis).toHaveBeenCalledTimes(1));
  });
});
