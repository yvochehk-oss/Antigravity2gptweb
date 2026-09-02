import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EntityTaxLedgerRecord, ProjectItem, ProjectTaxAnalysisRecord } from '../types';
import {
  fetchEntityTaxLedger,
  fetchJson,
  fetchProjectCounterparties,
  fetchProjectTaxAnalysis,
  fetchRiskEvents,
  postJson,
} from '../api';
import App from '../App';
import { AiDecisionCenterView } from './AiDecisionCenterView';
import { ProjectDetailView } from './ProjectDetailView';
import { TaxLedgerView } from './TaxLedgerView';
import { TaxPlanningView } from './TaxPlanningView';

const apiMocks = vi.hoisted(() => ({
  askProjectAi: vi.fn(),
  fetchAiModelStatus: vi.fn(),
  fetchAuditLogs: vi.fn(),
  fetchConfiguredProjects: vi.fn(),
  fetchEntityTaxLedger: vi.fn(),
  fetchRiskEvents: vi.fn(),
  rebuildTaxLedger: vi.fn(),
  extractAiExecutionMetadata: vi.fn(() => ({})),
  runAiReview: vi.fn(),
  runHealthCheck: vi.fn(),
  fetchJson: vi.fn(),
  postJson: vi.fn(),
  fetchProjectCounterparties: vi.fn(),
  fetchProjectTaxAnalysis: vi.fn(),
  deleteProjectData: vi.fn(),
}));

vi.mock('../api', () => {
  class ApiError extends Error {
    readonly status: number;

    constructor(message: string, status = 0) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }

  return {
    ApiError,
    ...apiMocks,
  };
});

vi.mock('./Sidebar', () => ({
  Sidebar: (props: any) => (
    <nav>
      <button type="button" data-testid="nav-dashboard" onClick={() => props.onSelectTab('dashboard')}>dashboard</button>
      <button type="button" data-testid="nav-tax-ledger" onClick={() => props.onSelectTab('tax-ledger')}>tax-ledger</button>
    </nav>
  ),
}));

vi.mock('./Header', () => ({ Header: () => null }));
vi.mock('./DashboardView', () => ({
  DashboardView: (props: any) => (
    <div data-testid="dashboard-view">
      <span data-testid="project-status">{props.dataStatus}</span>
      <span data-testid="entity-status">{props.taxLedgerStatus}</span>
      <span data-testid="project-name">{props.projects[0]?.name ?? ''}</span>
      <button type="button" data-testid="project-retry" onClick={props.onRetry}>retry</button>
    </div>
  ),
}));
vi.mock('./AiAssistantDrawer', () => ({ AiAssistantDrawer: () => null }));
vi.mock('./NewTaxRecordModal', () => ({ NewTaxRecordModal: () => null }));
vi.mock('./ExportReportModal', () => ({ ExportReportModal: () => null }));
vi.mock('./SettingsModal', () => ({ DEFAULT_SETTINGS: {} }));

function project(id: number, name = `Project ${id}`, totalBudget = 1000): ProjectItem {
  return {
    id: String(id),
    numericId: id,
    projectCode: `P-${String(id).padStart(3, '0')}`,
    name,
    constructionStage: '施工中',
    healthGrade: '未知',
    totalBudget,
    spentAmount: 300,
    remainingBudget: Math.max(totalBudget - 300, 0),
    progressPercent: 30,
    taxRiskGrade: '未知',
    isOverBudget: false,
    managerName: '测试经理',
    location: '成都',
    teamAvatars: [],
    costItems: [],
  };
}

const projects: ProjectItem[] = [
  project(1, 'Phase 2 项目一', 1000),
  project(2, 'Phase 2 项目二', 2000),
];

function statutoryRecord(entityName = '成都建工测试法人'): EntityTaxLedgerRecord {
  return {
    id: `A01-2026-08-${entityName}`,
    period: '2026-08',
    entityId: 1,
    reportingPartyId: 1,
    entityCode: 'A01',
    entityName,
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
}

const projectAnalysis: ProjectTaxAnalysisRecord = {
  projectId: 1,
  projectCode: 'P-001',
  projectName: 'Phase 2 项目一',
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
  internalEliminatedNet: 120,
  internalEliminatedVat: 10.8,
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

const ledgerProps = {
  dataStatus: 'READY' as const,
  dataStatusMessage: '',
  onRetry: vi.fn(),
  onOpenNewRecordModal: vi.fn(),
  onOpenExportModal: vi.fn(),
  onAskAiAboutRisk: vi.fn(),
  onRebuildTaxLedger: vi.fn(async () => undefined),
  isRebuilding: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.spyOn(window, 'confirm').mockReturnValue(true);

  apiMocks.fetchAiModelStatus.mockResolvedValue({ state: 'READY', message: 'ready', endpoints: [] });
  apiMocks.fetchAuditLogs.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.fetchConfiguredProjects.mockResolvedValue([projects[0]]);
  apiMocks.fetchEntityTaxLedger.mockResolvedValue({ items: [statutoryRecord()], status: 'READY', message: '' });
  apiMocks.fetchRiskEvents.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.rebuildTaxLedger.mockResolvedValue({ status: 'READY', period: '2026-08', rowCount: 1 });
  apiMocks.fetchJson.mockResolvedValue({ status: 'READY' });
  apiMocks.postJson.mockResolvedValue({ recommended: {}, scenarios: [] });
  apiMocks.fetchProjectTaxAnalysis.mockResolvedValue({ status: 'READY', message: '', item: projectAnalysis });
  apiMocks.fetchProjectCounterparties.mockResolvedValue({ status: 'EMPTY', message: '', items: [], total: 0 });
});

describe('Phase 2 dual-domain invariants T1-T10', () => {
  it('T1: Entity fail / Project ready keeps the Project view fully usable', async () => {
    vi.mocked(fetchEntityTaxLedger).mockRejectedValueOnce(new Error('entity unavailable'));

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('READY'));
    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('UNAVAILABLE'));
    expect(screen.getByTestId('project-name')).toHaveTextContent('Phase 2 项目一');
    expect(screen.getByTestId('project-retry')).toBeEnabled();
  });

  it('T2: Project fail / Entity ready keeps the statutory Entity view fully usable', async () => {
    apiMocks.fetchConfiguredProjects.mockRejectedValueOnce(new Error('project unavailable'));
    vi.mocked(fetchEntityTaxLedger).mockResolvedValueOnce({ items: [statutoryRecord('法人 A')], status: 'READY', message: '' });

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('UNAVAILABLE'));
    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('READY'));
    fireEvent.click(screen.getByTestId('nav-tax-ledger'));

    expect(await screen.findByText('LEGAL_ENTITY_STATUTORY 法人法定申报口径')).toBeInTheDocument();
    expect(screen.getByText('法人 A')).toBeInTheDocument();
    expect(screen.getAllByText('¥14').length).toBeGreaterThan(0);
  });

  it('T3: Abort race prevents a slow stale Entity response from overwriting the newer ready Entity data', async () => {
    const stale = deferred<any>();
    let staleSignal: AbortSignal | undefined;
    vi.mocked(fetchEntityTaxLedger)
      .mockImplementationOnce((signal?: AbortSignal) => {
        staleSignal = signal;
        return stale.promise;
      })
      .mockResolvedValueOnce({ items: [statutoryRecord('新法人数据')], status: 'READY', message: '' });

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await waitFor(() => expect(vi.mocked(fetchEntityTaxLedger).mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(staleSignal?.aborted).toBe(true);
    fireEvent.click(screen.getByTestId('nav-tax-ledger'));
    expect(await screen.findByText('新法人数据')).toBeInTheDocument();

    await act(async () => {
      stale.resolve({ items: [statutoryRecord('旧法人数据')], status: 'READY', message: '' });
      await stale.promise;
      await Promise.resolve();
    });

    expect(screen.getByText('新法人数据')).toBeInTheDocument();
    expect(screen.queryByText('旧法人数据')).not.toBeInTheDocument();
  });

  it('T4: Risk reloads only when the project ID scope changes', async () => {
    apiMocks.fetchConfiguredProjects
      .mockResolvedValueOnce([project(1, 'Alpha', 100)])
      .mockResolvedValueOnce([project(1, 'Alpha renamed', 999)])
      .mockResolvedValueOnce([project(2, 'Beta', 300)]);

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Alpha'));
    await waitFor(() => expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fetchRiskEvents).mock.calls[0][0]).toBe(1);

    fireEvent.click(screen.getByTestId('project-retry'));
    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Alpha renamed'));
    expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('project-retry'));
    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Beta'));
    await waitFor(() => expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(fetchRiskEvents).mock.calls[1][0]).toBe(2);
  });

  it('T5: statutory EntityTaxLedgerRecord and TaxLedgerView contain no legacy P&L fields', () => {
    const typesSource = readFileSync(resolve(process.cwd(), 'src/types.ts'), 'utf8');
    const ledgerSource = readFileSync(resolve(process.cwd(), 'src/components/TaxLedgerView.tsx'), 'utf8');
    const match = typesSource.match(/export interface EntityTaxLedgerRecord\s*\{([\s\S]*?)\n\}/);

    expect(match).not.toBeNull();
    const recordBlock = match?.[1] ?? '';
    for (const field of ['revenue', 'realCost', 'estimatedProfit', 'estimatedCit', 'vatPayable']) {
      expect(recordBlock).not.toMatch(new RegExp(`\\b${field}\\??\\s*:`));
      expect(ledgerSource).not.toMatch(new RegExp(`\\.${field}\\b`));
    }

    render(<TaxLedgerView {...ledgerProps} records={[statutoryRecord()]} />);
    expect(screen.getByText('LEGAL_ENTITY_STATUTORY 法人法定申报口径')).toBeInTheDocument();
    expect(screen.queryByText('营业/计税收入')).not.toBeInTheDocument();
    expect(screen.queryByText('真实成本合计')).not.toBeInTheDocument();
    expect(screen.queryByText('预计利润')).not.toBeInTheDocument();
    expect(screen.queryByText('预计所得税')).not.toBeInTheDocument();
  });

  it('T6: ProjectDetailView exposes PROJECT_BOUNDARY, signed VAT, pending/nondeductible VAT, and internal elimination', async () => {
    render(
      <ProjectDetailView
        project={projects[0]}
        onBack={vi.fn()}
        onOpenNewRecordModal={vi.fn()}
        onOpenExportModal={vi.fn()}
        onAskAiAboutRisk={vi.fn()}
      />,
    );

    expect(await screen.findByText('PROJECT_BOUNDARY 项目管理口径 · 非申报依据')).toBeInTheDocument();
    expect(screen.getByTestId('signed-vat-position')).toHaveTextContent('36.00');
    expect(screen.getByTestId('pending-input-vat')).toHaveTextContent('10.00');
    expect(screen.getByTestId('nondeductible-input-vat')).toHaveTextContent('6.00');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('抵消净额');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('120.00');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('抵消 VAT');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('10.80');
  });

  it('T7: Planning mount is 0 POST, project switch is 0 POST, explicit Run is exactly 1 persist:false POST', async () => {
    const view = render(<TaxPlanningView projects={projects} selectedProjectId="1" />);
    await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalledWith('/api/projects/1/system-penetration', expect.anything()));
    expect(vi.mocked(postJson)).not.toHaveBeenCalled();

    view.rerender(<TaxPlanningView projects={projects} selectedProjectId="2" />);
    await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalledWith('/api/projects/2/system-penetration', expect.anything()));
    expect(vi.mocked(postJson)).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: 'C8.7 用户假设' } });
    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '200000' } });
    fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));

    await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postJson)).toHaveBeenCalledWith(
      '/api/projects/2/allocation-planning/recommend',
      expect.objectContaining({ package_name: 'C8.7 用户假设', package_amount: 200000, persist: false }),
    );
  });

  it('T8: Planning always declares SCENARIO / SIMULATION / NOT FILING BASIS', () => {
    render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />);
    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));

    expect(screen.getByText('SCENARIO · 模拟方案 | SIMULATION · NOT FILING BASIS')).toBeInTheDocument();
    expect(screen.getByText('本视图基于用户输入假设，不代表项目真实经营结果，不属于法人法定申报依据。')).toBeInTheDocument();
  });

  it('T9: Scenario state never mutates Project actual KPI or statutory VAT KPI', async () => {
    const statutory = statutoryRecord();
    render(
      <div>
        <ProjectDetailView
          project={projects[0]}
          onBack={vi.fn()}
          onOpenNewRecordModal={vi.fn()}
          onOpenExportModal={vi.fn()}
          onAskAiAboutRisk={vi.fn()}
        />
        <TaxLedgerView {...ledgerProps} records={[statutory]} />
        <TaxPlanningView projects={projects} selectedProjectId="1" />
      </div>,
    );

    expect(await screen.findByTestId('signed-vat-position')).toHaveTextContent('36.00');
    expect(screen.getAllByText('¥14').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '只属于 Scenario 的方案' } });
    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '999999' } });
    fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));
    await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId('signed-vat-position')).toHaveTextContent('36.00');
    expect(screen.getAllByText('¥14').length).toBeGreaterThan(0);
    expect(projects[0].totalBudget).toBe(1000);
    expect(statutory.vatPayableAfterPrepayment).toBe(14);
  });

  it('T10: React.StrictMode creates no automatic or persisted Scenario side effect', async () => {
    render(
      <StrictMode>
        <TaxPlanningView projects={projects} selectedProjectId="1" />
      </StrictMode>,
    );

    await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalled());
    expect(vi.mocked(postJson)).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: 'StrictMode 用户假设' } });
    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));

    await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postJson)).toHaveBeenLastCalledWith(
      '/api/projects/1/allocation-planning/recommend',
      expect.objectContaining({ persist: false }),
    );
  });
});
