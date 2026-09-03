import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EntityTaxLedgerRecord, ProjectItem, ProjectTaxAnalysisRecord } from '../types';
import { fetchJson, fetchProjectCounterparties, fetchProjectTaxAnalysis, fetchRiskEvents, postJson } from '../api';
import { fetchLegalEntityStatutoryVatCollection } from '../legalEntityApi';
import App from '../App';
import { AiDecisionCenterView } from './AiDecisionCenterView';
import { ProjectDetailView } from './ProjectDetailView';
import { TaxLedgerView } from './TaxLedgerView';
import { TaxPlanningView } from './TaxPlanningView';

const apiMocks = vi.hoisted(() => ({
  askProjectAi: vi.fn(), fetchAiModelStatus: vi.fn(), fetchAuditLogs: vi.fn(), fetchConfiguredProjects: vi.fn(),
  fetchEntityTaxLedger: vi.fn(), fetchRiskEvents: vi.fn(), rebuildTaxLedger: vi.fn(), extractAiExecutionMetadata: vi.fn(() => ({})),
  runAiReview: vi.fn(), runHealthCheck: vi.fn(), fetchJson: vi.fn(), postJson: vi.fn(), fetchProjectCounterparties: vi.fn(),
  fetchProjectTaxAnalysis: vi.fn(), deleteProjectData: vi.fn(),
}));
const legalEntityApiMocks = vi.hoisted(() => ({
  fetchLegalEntities: vi.fn(), fetchLegalEntityFactPeriods: vi.fn(), fetchLegalEntityStatutoryVat: vi.fn(),
  fetchLegalEntityStatutoryVatCollection: vi.fn(), rebuildLegalEntityStatutoryVatCollection: vi.fn(),
}));

vi.mock('../api', () => {
  class ApiError extends Error { readonly status: number; constructor(message: string, status = 0) { super(message); this.status = status; } }
  return { ApiError, ...apiMocks };
});
vi.mock('../legalEntityApi', () => ({ ...legalEntityApiMocks }));
vi.mock('./Sidebar', () => ({ Sidebar: (props: any) => <nav><button data-testid="nav-dashboard" onClick={() => props.onSelectTab('dashboard')}>总览</button><button data-testid="nav-tax-ledger" onClick={() => props.onSelectTab('tax-ledger')}>法定税务</button></nav> }));
vi.mock('./Header', () => ({ Header: () => null }));
vi.mock('./DashboardView', () => ({ DashboardView: (props: any) => <div><span data-testid="project-status">{props.dataStatus}</span><span data-testid="entity-status">{props.taxLedgerStatus}</span><span data-testid="project-name">{props.projects[0]?.name ?? ''}</span><button data-testid="project-retry" onClick={props.onRetry}>重载</button></div> }));
vi.mock('./AiAssistantDrawer', () => ({ AiAssistantDrawer: () => null }));
vi.mock('./NewTaxRecordModal', () => ({ NewTaxRecordModal: () => null }));
vi.mock('./ExportReportModal', () => ({ ExportReportModal: () => null }));
vi.mock('./SettingsModal', () => ({ DEFAULT_SETTINGS: {} }));

function project(id: number, name = `项目 ${id}`, totalBudget = 1000): ProjectItem {
  return { id: String(id), numericId: id, projectCode: `P-${String(id).padStart(3, '0')}`, name, constructionStage: '施工中', healthGrade: '未知', totalBudget, spentAmount: 300, remainingBudget: Math.max(totalBudget - 300, 0), progressPercent: 30, taxRiskGrade: '未知', isOverBudget: false, managerName: '测试经理', location: '成都', teamAvatars: [], costItems: [] };
}
const projects = [project(1, '阶段二项目一', 1000), project(2, '阶段二项目二', 2000)];
function statutoryRecord(entityName = '成都建工测试法人'): EntityTaxLedgerRecord {
  return { id: `A01-2026-08-${entityName}`, period: '2026-08', entityId: 1, reportingPartyId: 1, entityCode: 'A01', entityName, businessRole: '施工', legalEntity: true, scope: 'LEGAL_ENTITY_STATUTORY', isFilingBasis: true, sourceOfTruth: 'entity_vat_ledgers', openingInputCredit: 10, outputVat: 90, inputVat: 54, taxPrepayment: 12, vatPayableBeforePrepayment: 26, closingInputCredit: 3, vatPayableAfterPrepayment: 14, unappliedTaxPrepayment: 0, calculationRunId: 101, runKind: 'NORMAL', runStatus: 'SUCCEEDED', rulesetVersion: 'v3', periodState: 'OPEN', inputSnapshotSha256: 'input-sha', resultSha256: 'result-sha', legalEntityVatIdentityOk: true, lineageComponents: [], dataStatus: 'READY', dataGaps: [], trusted: true };
}
const projectAnalysis: ProjectTaxAnalysisRecord = {
  projectId: 1, projectCode: 'P-001', projectName: '阶段二项目一', period: '2026-08', entityCode: 'A01', scope: 'PROJECT_BOUNDARY', isFilingBasis: false,
  outInvoiceNet: 1000, outInvoiceVat: 90, inInvoiceNet: 600, inInvoiceVat: 70, deductibleInputVat: 54, nondeductibleInputVat: 6, pendingInputVat: 10,
  signedVatPosition: 36, internalEliminatedNet: 120, internalEliminatedVat: 10.8, inputVatAccounted: 60, inputVatUnaccounted: 10, inputVatIdentityOk: true,
  realCost: 600, invoiceCount: 5, sourceOfTruth: 'analytics_canonical_facts_current', legacyTablesUsed: false, realCostBasis: 'PAYMENT_CONFIRMED', dataGaps: [],
};
const ledgerProps = { dataStatus: 'READY' as const, dataStatusMessage: '', onRetry: vi.fn(), onOpenNewRecordModal: vi.fn(), onOpenExportModal: vi.fn(), onAskAiAboutRisk: vi.fn(), onRebuildTaxLedger: vi.fn(async () => undefined), isRebuilding: false };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'confirm').mockReturnValue(true);
  apiMocks.fetchAiModelStatus.mockResolvedValue({ state: 'READY', message: '就绪', endpoints: [] });
  apiMocks.fetchAuditLogs.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.fetchConfiguredProjects.mockResolvedValue([projects[0]]);
  apiMocks.fetchEntityTaxLedger.mockResolvedValue({ items: [statutoryRecord()], status: 'READY', message: '' });
  apiMocks.fetchRiskEvents.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.rebuildTaxLedger.mockResolvedValue({ status: 'READY', period: '2026-08', rowCount: 1 });
  apiMocks.fetchJson.mockResolvedValue({ status: 'READY' }); apiMocks.postJson.mockResolvedValue({ recommended: {}, scenarios: [] });
  apiMocks.fetchProjectTaxAnalysis.mockResolvedValue({ status: 'READY', message: '', item: projectAnalysis });
  apiMocks.fetchProjectCounterparties.mockResolvedValue({ status: 'EMPTY', message: '', items: [], total: 0 });
  legalEntityApiMocks.fetchLegalEntityStatutoryVatCollection.mockResolvedValue({ items: [statutoryRecord()], status: 'READY', message: '' });
  legalEntityApiMocks.rebuildLegalEntityStatutoryVatCollection.mockResolvedValue({ status: 'READY', period: '2026-08', rowCount: 1, failedCount: 0, message: '' });
});

describe('Phase 2 dual-domain invariants T1-T10', () => {
  it('T1 entity failure never disables the project domain', async () => {
    vi.mocked(fetchLegalEntityStatutoryVatCollection).mockRejectedValueOnce(new Error('entity unavailable'));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('READY'));
    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('UNAVAILABLE'));
    expect(screen.getByTestId('project-name')).toHaveTextContent('阶段二项目一');
  });

  it('T2 project failure never disables the statutory entity domain', async () => {
    apiMocks.fetchConfiguredProjects.mockRejectedValueOnce(new Error('project unavailable'));
    vi.mocked(fetchLegalEntityStatutoryVatCollection).mockResolvedValueOnce({ items: [statutoryRecord('法人 A')], status: 'READY', message: '' });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('UNAVAILABLE'));
    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('READY'));
    fireEvent.click(screen.getByTestId('nav-tax-ledger'));
    expect(await screen.findByText('法人法定申报口径')).toBeInTheDocument();
    expect(screen.getByText('法人 A')).toBeInTheDocument();
  });

  it('T3 abort race keeps the newest entity response', async () => {
    const stale = deferred<any>(); let staleSignal: AbortSignal | undefined;
    vi.mocked(fetchLegalEntityStatutoryVatCollection).mockImplementationOnce((signal?: AbortSignal) => { staleSignal = signal; return stale.promise; }).mockResolvedValueOnce({ items: [statutoryRecord('新法人数据')], status: 'READY', message: '' });
    render(<StrictMode><App /></StrictMode>);
    await waitFor(() => expect(vi.mocked(fetchLegalEntityStatutoryVatCollection).mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(staleSignal?.aborted).toBe(true); fireEvent.click(screen.getByTestId('nav-tax-ledger'));
    expect(await screen.findByText('新法人数据')).toBeInTheDocument();
    await act(async () => { stale.resolve({ items: [statutoryRecord('旧法人数据')], status: 'READY', message: '' }); await stale.promise; });
    expect(screen.queryByText('旧法人数据')).not.toBeInTheDocument();
  });

  it('T4 risk reloads only when project identity changes', async () => {
    apiMocks.fetchConfiguredProjects.mockResolvedValueOnce([project(1, '甲项目', 100)]).mockResolvedValueOnce([project(1, '甲项目改名', 999)]).mockResolvedValueOnce([project(2, '乙项目', 300)]);
    render(<App />); await waitFor(() => expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('project-retry')); await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('甲项目改名')); expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('project-retry')); await waitFor(() => expect(vi.mocked(fetchRiskEvents)).toHaveBeenCalledTimes(2)); expect(vi.mocked(fetchRiskEvents).mock.calls[1][0]).toBe(2);
  });

  it('T5 statutory record and view contain no legacy profit-and-loss fields', () => {
    const typesSource = readFileSync(resolve(process.cwd(), 'src/types.ts'), 'utf8'); const ledgerSource = readFileSync(resolve(process.cwd(), 'src/components/TaxLedgerView.tsx'), 'utf8');
    const recordBlock = typesSource.match(/export interface EntityTaxLedgerRecord\s*\{([\s\S]*?)\n\}/)?.[1] ?? '';
    for (const field of ['revenue', 'realCost', 'estimatedProfit', 'estimatedCit', 'vatPayable']) { expect(recordBlock).not.toMatch(new RegExp(`\\b${field}\\??\\s*:`)); expect(ledgerSource).not.toMatch(new RegExp(`\\.${field}\\b`)); }
    render(<TaxLedgerView {...ledgerProps} records={[statutoryRecord()]} />); expect(screen.getByText('法人法定申报口径')).toBeInTheDocument();
  });

  it('T6 project boundary exposes management tax position and input-tax split in Chinese', async () => {
    render(<ProjectDetailView project={projects[0]} onBack={vi.fn()} onOpenNewRecordModal={vi.fn()} onOpenExportModal={vi.fn()} onAskAiAboutRisk={vi.fn()} />);
    expect(await screen.findByText('项目管理边界 · 项目管理口径 · 非申报依据')).toBeInTheDocument();
    expect(screen.getByTestId('signed-vat-position')).toHaveTextContent('36.00'); expect(screen.getByTestId('pending-input-vat')).toHaveTextContent('10.00'); expect(screen.getByTestId('nondeductible-input-vat')).toHaveTextContent('6.00');
    expect(screen.getByTestId('internal-elimination-card')).toHaveTextContent('抵消增值税');
  });

  it('T7 planning mount and project switch perform zero automatic writes; explicit run writes once with persist false', async () => {
    const view = render(<TaxPlanningView projects={projects} selectedProjectId="1" />); await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalledWith('/api/projects/1/system-penetration', expect.anything())); expect(vi.mocked(postJson)).not.toHaveBeenCalled();
    view.rerender(<TaxPlanningView projects={projects} selectedProjectId="2" />); await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalledWith('/api/projects/2/system-penetration', expect.anything())); expect(vi.mocked(postJson)).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '用户假设' } }); fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '200000' } }); fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));
    await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1)); expect(vi.mocked(postJson)).toHaveBeenCalledWith('/api/projects/2/allocation-planning/recommend', expect.objectContaining({ persist: false }));
  });

  it('T8 planning always declares simulation and non-filing basis in Chinese', () => {
    render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />); fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));
    expect(screen.getByText('模拟方案 · 非申报依据')).toBeInTheDocument(); expect(screen.getByText('本视图基于用户输入假设，不代表项目真实经营结果，不属于法人法定申报依据。')).toBeInTheDocument();
  });

  it('T9 simulation state never mutates project actuals or statutory tax', async () => {
    const statutory = statutoryRecord(); render(<div><ProjectDetailView project={projects[0]} onBack={vi.fn()} onOpenNewRecordModal={vi.fn()} onOpenExportModal={vi.fn()} onAskAiAboutRisk={vi.fn()} /><TaxLedgerView {...ledgerProps} records={[statutory]} /><TaxPlanningView projects={projects} selectedProjectId="1" /></div>);
    expect(await screen.findByTestId('signed-vat-position')).toHaveTextContent('36.00'); expect(screen.getAllByText('¥14').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '只属于模拟的方案' } }); fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '999999' } }); fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' })); await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1));
    expect(projects[0].totalBudget).toBe(1000); expect(statutory.vatPayableAfterPrepayment).toBe(14);
  });

  it('T10 strict mode creates no automatic or persisted simulation side effect', async () => {
    render(<StrictMode><TaxPlanningView projects={projects} selectedProjectId="1" /></StrictMode>); await waitFor(() => expect(vi.mocked(fetchJson)).toHaveBeenCalled()); expect(vi.mocked(postJson)).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '严格模式用户假设' } }); fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));
    await waitFor(() => expect(vi.mocked(postJson)).toHaveBeenCalledTimes(1)); expect(vi.mocked(postJson)).toHaveBeenLastCalledWith('/api/projects/1/allocation-planning/recommend', expect.objectContaining({ persist: false }));
  });
});
