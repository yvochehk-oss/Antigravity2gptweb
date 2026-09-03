import type { ComponentProps } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { EntityTaxLedgerRecord, ProjectItem, RiskEvent } from '../types';
import { DashboardView } from './DashboardView';

const projects = [{
  id: '1', numericId: 1, projectCode: 'P-001', name: '天府项目', constructionStage: '施工中', healthGrade: 'A', totalBudget: 1000, spentAmount: 600, remainingBudget: 400, progressPercent: 60, taxRiskGrade: '低', isOverBudget: false, managerName: '项目经理', location: '成都', teamAvatars: [], costItems: [],
}] as ProjectItem[];

const ledgerRecords = [{
  id: 'A08-2026-09', period: '2026-09', entityId: 8, reportingPartyId: 8, entityCode: 'A08', entityName: 'A08 法人', businessRole: '施工', legalEntity: true, scope: 'LEGAL_ENTITY_STATUTORY', isFilingBasis: true, sourceOfTruth: 'entity_vat_ledgers', openingInputCredit: 10, outputVat: 90, inputVat: 40, taxPrepayment: 5, vatPayableBeforePrepayment: 40, closingInputCredit: 8, vatPayableAfterPrepayment: 35, unappliedTaxPrepayment: 0, calculationRunId: 1, runKind: 'MONTHLY', runStatus: 'SUCCESS', rulesetVersion: 'v1', periodState: 'OPEN', inputSnapshotSha256: 'a', resultSha256: 'b', legalEntityVatIdentityOk: true, lineageComponents: [], dataStatus: 'READY', dataGaps: [], trusted: true,
}] as EntityTaxLedgerRecord[];

const risks = [{
  id: 'R-1', projectName: '天府项目', entityName: 'A08 法人', riskType: '发票', severity: '中度', triggerTime: '2026-09-01', description: '风险', auditSuggestions: '复核', status: '待处置', handler: '未分配',
}] as RiskEvent[];

function renderDashboard(overrides: Partial<ComponentProps<typeof DashboardView>> = {}) {
  const props: ComponentProps<typeof DashboardView> = {
    projects,
    dataStatus: 'READY',
    dataStatusMessage: '项目已就绪',
    onRetry: vi.fn(),
    onSelectProject: vi.fn(),
    onOpenEntityCorporate: vi.fn(),
    onOpenRiskCenter: vi.fn(),
    onOpenExportModal: vi.fn(),
    riskEvents: risks,
    riskStatus: 'READY',
    riskStatusMessage: '风险已就绪',
    taxLedgerStatus: 'READY',
    taxLedgerStatusMessage: '法人台账已就绪',
    taxLedgerRecords: ledgerRecords,
    ...overrides,
  };
  const view = render(<DashboardView {...props} />);
  return { props, ...view };
}

describe('DashboardView dual-core executive summaries', () => {
  it('renders statutory and project scopes as separate KPI sections without calling contract-cost gap real profit', () => {
    renderDashboard();
    const statutory = screen.getByRole('region', { name: '法人主体法定税务总览' });
    const project = screen.getByRole('region', { name: '项目工程管理口径总览' });

    expect(within(statutory).getByText('法人法定申报 · 法定申报口径')).toBeInTheDocument();
    expect(within(statutory).getByText('本期法定销项税额合计')).toBeInTheDocument();
    expect(within(statutory).getByText('本期法定进项税额合计')).toBeInTheDocument();
    expect(within(statutory).getByText('本期法定应纳税额合计')).toBeInTheDocument();
    expect(within(statutory).getByText('期末留抵税额合计')).toBeInTheDocument();
    expect(within(statutory).queryByText(/项目外部收入总额/)).not.toBeInTheDocument();

    expect(within(project).getByText('项目管理边界 · 项目管理／测算口径')).toBeInTheDocument();
    expect(within(project).getByText('项目接口项目数量')).toBeInTheDocument();
    expect(within(project).getByText('项目外部收入总额（合同额）')).toBeInTheDocument();
    expect(within(project).getByText('真实归集成本')).toBeInTheDocument();
    expect(within(project).getByText('合同额与真实成本差额')).toBeInTheDocument();
    expect(within(project).getByText('仅为合同与成本差额，非最终利润')).toBeInTheDocument();
    expect(within(project).queryByText(/法定应纳税额/)).not.toBeInTheDocument();
    expect(screen.queryByText('真实利润')).not.toBeInTheDocument();
  });

  it('keeps the page title independent from the action controls', () => {
    const { container } = renderDashboard();
    const title = container.querySelector('[data-page-title="dashboard"]');
    const controls = container.querySelector('[data-page-controls="dashboard"]');
    expect(title).toBeInTheDocument();
    expect(controls).toBeInTheDocument();
    expect(title?.parentElement).toBe(controls?.parentElement);
    expect(title).not.toContainElement(controls);
  });

  it('keeps READY empty statutory state visibly empty instead of fabricating zero tax', () => {
    renderDashboard({ taxLedgerStatus: 'READY', taxLedgerRecords: [] });
    const statutory = screen.getByRole('region', { name: '法人主体法定税务总览' });
    expect(within(statutory).getAllByText('—')).toHaveLength(5);
    expect(statutory).not.toHaveTextContent('¥ 0 元');
    expect(statutory).not.toHaveTextContent('0 个');
  });

  it('keeps entity and project navigation callbacks independent', () => {
    const onOpenEntityCorporate = vi.fn();
    const onSelectProject = vi.fn();
    renderDashboard({ onOpenEntityCorporate, onSelectProject });
    fireEvent.click(screen.getByRole('button', { name: '进入法人经营画像' }));
    expect(onOpenEntityCorporate).toHaveBeenCalledWith('A08');
    fireEvent.click(screen.getByRole('button', { name: /天府项目/ }));
    expect(onSelectProject).toHaveBeenCalledWith('1');
  });

  it('does not hide one domain when the other domain is unavailable', () => {
    renderDashboard({ projects: [], dataStatus: 'UNAVAILABLE', dataStatusMessage: '项目接口不可用', taxLedgerStatus: 'READY', taxLedgerRecords: ledgerRecords });
    const statutory = screen.getByRole('region', { name: '法人主体法定税务总览' });
    expect(statutory).toHaveTextContent('法人主体数量');
    expect(statutory).toHaveTextContent('1 个');
    expect(screen.getByRole('region', { name: '项目工程管理口径总览' })).toHaveTextContent('项目接口不可用');
    const health = screen.getByRole('region', { name: '风险与数据源健康状态' });
    expect(within(health).getByText('数据源健康状态')).toBeInTheDocument();
    expect(within(health).getByText('法人法定申报增值税')).toBeInTheDocument();
    expect(within(health).getByText('项目管理边界')).toBeInTheDocument();
  });
});
