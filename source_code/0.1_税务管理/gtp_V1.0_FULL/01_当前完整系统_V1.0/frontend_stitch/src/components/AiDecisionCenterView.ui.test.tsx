import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProjectItem } from '../types';
import { fetchJson, postJson } from '../api';
import { AiDecisionCenterView } from './AiDecisionCenterView';

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  extractAiExecutionMetadata: vi.fn(() => ({})),
  runAiReview: vi.fn(),
  runHealthCheck: vi.fn(),
  fetchJson: vi.fn(),
  postJson: vi.fn(),
}));

const projects: ProjectItem[] = [{
  id: '1', numericId: 1, projectCode: 'P-001', name: '统一决策中心测试项目', constructionStage: '施工中', healthGrade: '未知', totalBudget: 1000, spentAmount: 0, remainingBudget: 1000, progressPercent: 10, taxRiskGrade: '未知', isOverBudget: null, managerName: '—', location: '成都', teamAvatars: [], costItems: [],
}];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchJson).mockResolvedValue({ status: 'READY' });
  vi.mocked(postJson).mockResolvedValue({ recommended: {}, scenarios: [] });
});

describe('AiDecisionCenterView workspace boundaries', () => {
  it('shows the Chinese fact-based label only for backend-confirmed canonical facts and otherwise degrades', () => {
    const { rerender } = render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" reviewResult={{ data_source: 'CANONICAL_FACTS' }} />);

    expect(screen.getByText('基于规范事实')).toBeInTheDocument();
    expect(screen.queryByText('降级运行：当前智能审查上下文尚未确认使用规范事实模式')).not.toBeInTheDocument();

    rerender(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" reviewResult={{ data_source: 'LEGACY_OR_UNKNOWN' }} />);

    expect(screen.queryByText('基于规范事实')).not.toBeInTheDocument();
    expect(screen.getByText('降级运行：当前智能审查上下文尚未确认使用规范事实模式')).toBeInTheDocument();
  });

  it('keeps the planning workspace explicitly marked as simulation and not filing basis in Chinese', () => {
    render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />);
    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));

    expect(screen.getByText('模拟方案 · 非申报依据')).toBeInTheDocument();
    expect(screen.getByText('本视图基于用户输入假设，不代表项目真实经营结果，不属于法人法定申报依据。')).toBeInTheDocument();
  });

  it('keeps the page title independent from the workspace controls', () => {
    const { container } = render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />);
    const title = container.querySelector('[data-page-title="ai-decision"]');
    const controls = container.querySelector('[data-page-controls="ai-decision"]');
    expect(title).toBeInTheDocument();
    expect(controls).toBeInTheDocument();
    expect(title?.parentElement).toBe(controls?.parentElement);
    expect(title).not.toContainElement(controls);
  });

  it('switches decision-center tabs without any automatic Planning POST request', async () => {
    render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />);

    await waitFor(() => expect(fetchJson).toHaveBeenCalledWith('/api/projects/1/system-penetration', expect.anything()));
    expect(postJson).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));
    fireEvent.click(screen.getByRole('tab', { name: '综合体检与辅助研判' }));
    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));

    expect(postJson).not.toHaveBeenCalled();
  });

  it('preserves isolated review and simulation state across workspace switches', () => {
    render(<AiDecisionCenterView projects={projects} dataStatus="READY" selectedProjectId="1" />);

    const reviewInstruction = screen.getByLabelText('指导意见');
    fireEvent.change(reviewInstruction, { target: { value: '仅属于审查工作区的研判备注' } });

    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));
    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '仅属于模拟方案的用户假设' } });
    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '88000' } });

    fireEvent.click(screen.getByRole('tab', { name: '综合体检与辅助研判' }));
    expect(screen.getByLabelText('指导意见')).toHaveValue('仅属于审查工作区的研判备注');

    fireEvent.click(screen.getByRole('tab', { name: '项目筹划沙盘' }));
    expect(screen.getByLabelText('业务包名称')).toHaveValue('仅属于模拟方案的用户假设');
    expect(screen.getByLabelText('业务包金额（元）')).toHaveValue(88000);
    expect(postJson).not.toHaveBeenCalled();
  });
});
