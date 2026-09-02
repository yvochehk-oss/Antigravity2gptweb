import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  askProjectAi: vi.fn(),
  fetchAiModelStatus: vi.fn(),
  fetchAuditLogs: vi.fn(),
  fetchConfiguredProjects: vi.fn(),
  fetchEntityTaxLedger: vi.fn(),
  fetchRiskEvents: vi.fn(),
  rebuildTaxLedger: vi.fn(),
}));

const dashboardRenderSpy = vi.hoisted(() => vi.fn());

vi.mock('../../api', () => {
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

vi.mock('../Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../Header', () => ({ Header: () => null }));
vi.mock('../DashboardView', () => ({
  DashboardView: (props: any) => {
    dashboardRenderSpy({
      projectStatus: props.dataStatus,
      entityStatus: props.taxLedgerStatus,
      projectName: props.projects[0]?.name ?? '',
    });
    return (
      <div>
        <span data-testid="project-status">{props.dataStatus}</span>
        <span data-testid="entity-status">{props.taxLedgerStatus}</span>
        <span data-testid="project-name">{props.projects[0]?.name ?? ''}</span>
        <button type="button" data-testid="project-retry" onClick={props.onRetry}>retry</button>
      </div>
    );
  },
}));
vi.mock('../ProjectRepositoryView', () => ({ ProjectRepositoryView: () => null }));
vi.mock('../ProjectDetailView', () => ({ ProjectDetailView: () => null }));
vi.mock('../TaxLedgerView', () => ({ TaxLedgerView: () => null }));
vi.mock('../TaxPlanningView', () => ({ TaxPlanningView: () => null }));
vi.mock('../RiskCenterView', () => ({ RiskCenterView: () => null }));
vi.mock('../AiReviewView', () => ({ AiReviewView: () => null }));
vi.mock('../AuditView', () => ({ AuditView: () => null }));
vi.mock('../AiAssistantDrawer', () => ({ AiAssistantDrawer: () => null }));
vi.mock('../NewTaxRecordModal', () => ({ NewTaxRecordModal: () => null }));
vi.mock('../ExportReportModal', () => ({ ExportReportModal: () => null }));
vi.mock('../DataStatusCard', () => ({ DataStatusCard: () => null }));
vi.mock('../SettingsModal', () => ({ DEFAULT_SETTINGS: {} }));

import App from '../../App';

function project(id: number, name = `Project ${id}`, totalBudget = 100) {
  return {
    id: String(id),
    numericId: id,
    projectCode: `P-${id}`,
    name,
    totalBudget,
  } as any;
}

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

  apiMocks.fetchAiModelStatus.mockResolvedValue({ state: 'READY', message: 'ready', endpoints: [] });
  apiMocks.fetchAuditLogs.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.fetchConfiguredProjects.mockResolvedValue([project(1)]);
  apiMocks.fetchEntityTaxLedger.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.fetchRiskEvents.mockResolvedValue({ items: [], status: 'READY', message: '' });
  apiMocks.rebuildTaxLedger.mockResolvedValue({ status: 'READY', period: '2026-08', rowCount: 0 });
});

describe('App domain loading decoupling', () => {
  it('keeps Legal Entity VAT READY when the Project API fails', async () => {
    apiMocks.fetchConfiguredProjects.mockRejectedValueOnce(new Error('project unavailable'));

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('READY'));
    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('UNAVAILABLE'));
  });

  it('keeps Project READY when the Legal Entity VAT API fails', async () => {
    apiMocks.fetchEntityTaxLedger.mockRejectedValueOnce(new Error('entity vat unavailable'));

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('project-status')).toHaveTextContent('READY'));
    await waitFor(() => expect(screen.getByTestId('entity-status')).toHaveTextContent('UNAVAILABLE'));
  });

  it('reloads Risk only when the project ID scope changes', async () => {
    apiMocks.fetchConfiguredProjects
      .mockResolvedValueOnce([project(1, 'Alpha', 100)])
      .mockResolvedValueOnce([project(1, 'Alpha renamed', 999)])
      .mockResolvedValueOnce([project(2, 'Beta', 300)]);

    render(<App />);

    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Alpha'));
    await waitFor(() => expect(apiMocks.fetchRiskEvents).toHaveBeenCalledTimes(1));
    expect(apiMocks.fetchRiskEvents.mock.calls[0][0]).toBe(1);

    fireEvent.click(screen.getByTestId('project-retry'));
    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Alpha renamed'));
    expect(apiMocks.fetchRiskEvents).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('project-retry'));
    await waitFor(() => expect(screen.getByTestId('project-name')).toHaveTextContent('Beta'));
    await waitFor(() => expect(apiMocks.fetchRiskEvents).toHaveBeenCalledTimes(2));
    expect(apiMocks.fetchRiskEvents.mock.calls[1][0]).toBe(2);
  });

  it('aborts an in-flight Project request on unmount and ignores its late resolution', async () => {
    const pendingProjects = deferred<any[]>();
    let projectSignal: AbortSignal | undefined;
    apiMocks.fetchConfiguredProjects.mockImplementation((signal?: AbortSignal) => {
      projectSignal = signal;
      return pendingProjects.promise;
    });

    const view = render(<App />);
    await waitFor(() => expect(projectSignal).toBeDefined());
    const rendersBeforeUnmount = dashboardRenderSpy.mock.calls.length;

    view.unmount();
    expect(projectSignal?.aborted).toBe(true);

    await act(async () => {
      pendingProjects.resolve([project(9, 'Late project')]);
      await pendingProjects.promise;
      await Promise.resolve();
    });

    expect(dashboardRenderSpy).toHaveBeenCalledTimes(rendersBeforeUnmount);
  });
});
