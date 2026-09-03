import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TaxPlanningView } from './TaxPlanningView';
import type { ProjectItem } from '../types';
import { fetchJson, postJson } from '../api';

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {},
  fetchJson: vi.fn(),
  postJson: vi.fn(),
}));

const projects: ProjectItem[] = [
  {
    id: '1', numericId: 1, projectCode: 'P-001', name: '真实项目一', constructionStage: '施工中', healthGrade: '未知', totalBudget: 1000, spentAmount: 0, remainingBudget: 1000, progressPercent: 10, taxRiskGrade: '未知', isOverBudget: null, managerName: '—', location: '成都', teamAvatars: [], costItems: [],
  },
  {
    id: '2', numericId: 2, projectCode: 'P-002', name: '真实项目二', constructionStage: '施工中', healthGrade: '未知', totalBudget: 2000, spentAmount: 0, remainingBudget: 2000, progressPercent: 20, taxRiskGrade: '未知', isOverBudget: null, managerName: '—', location: '成都', teamAvatars: [], costItems: [],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchJson).mockResolvedValue({ status: 'READY' });
  vi.mocked(postJson).mockResolvedValue({ recommended: {}, scenarios: [] });
});

describe('TaxPlanningView scenario execution contract', () => {
  it('contains no synthetic preset registry or default preset fallback', () => {
    const sourcePath = resolve(process.cwd(), 'src/components/TaxPlanningView.tsx');
    const source = readFileSync(sourcePath, 'utf8');
    expect(source.includes('PROJECT_PACKAGE_PRESETS')).toBe(false);
    expect(source.includes('DEFAULT_PRESETS')).toBe(false);
    expect(source.includes('PackagePreset')).toBe(false);
    expect(source.includes('applyPreset')).toBe(false);
    expect(source.includes('currentPresets')).toBe(false);
  });

  it('starts with empty simulation amount and explicit user-assumption ratio defaults', async () => {
    render(<TaxPlanningView projects={projects} selectedProjectId="1" />);

    expect(screen.getByRole('note')).toHaveTextContent(
      '模拟方案 · 本视图基于用户输入假设，不代表当前项目真实经营结果，不属于法人法定申报依据。',
    );
    expect(screen.getByLabelText('业务包金额（元）')).toHaveValue(null);
    expect(screen.getByLabelText('系统内最低比例（%，用户假设）')).toHaveValue(0);
    expect(screen.getByLabelText('系统内最高比例（%，用户假设）')).toHaveValue(100);
    expect(screen.getByLabelText('偏好比例（可选，%，用户假设）')).toHaveValue(null);
    await waitFor(() => expect(fetchJson).toHaveBeenCalled());
  });

  it('mounts with a real context GET and zero planning POST requests', async () => {
    render(<TaxPlanningView projects={projects} selectedProjectId="1" />);

    await waitFor(() => {
      expect(fetchJson).toHaveBeenCalledWith(
        '/api/projects/1/system-penetration',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
    expect(postJson).not.toHaveBeenCalled();
  });

  it('switches project by clearing simulation input and issuing zero automatic POST requests', async () => {
    const { rerender } = render(<TaxPlanningView projects={projects} selectedProjectId="1" />);
    await waitFor(() => expect(fetchJson).toHaveBeenCalledWith('/api/projects/1/system-penetration', expect.anything()));

    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '8800' } });
    rerender(<TaxPlanningView projects={projects} selectedProjectId="2" />);

    await waitFor(() => expect(fetchJson).toHaveBeenCalledWith('/api/projects/2/system-penetration', expect.anything()));
    expect(screen.getByLabelText('业务包金额（元）')).toHaveValue(null);
    expect(postJson).not.toHaveBeenCalled();
  });

  it('posts exactly once only after explicit simulation click and sends persist false', async () => {
    render(<TaxPlanningView projects={projects} selectedProjectId="1" />);
    await waitFor(() => expect(fetchJson).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('业务包名称'), { target: { value: '用户输入劳务方案' } });
    fireEvent.change(screen.getByLabelText('业务包金额（元）'), { target: { value: '100000' } });
    fireEvent.click(screen.getByRole('button', { name: '测算筹划沙盘' }));

    await waitFor(() => expect(postJson).toHaveBeenCalledTimes(1));
    expect(postJson).toHaveBeenCalledWith(
      '/api/projects/1/allocation-planning/recommend',
      expect.objectContaining({
        package_name: '用户输入劳务方案',
        package_amount: 100000,
        internal_min_ratio: 0,
        internal_max_ratio: 1,
        persist: false,
      }),
    );
  });
});
