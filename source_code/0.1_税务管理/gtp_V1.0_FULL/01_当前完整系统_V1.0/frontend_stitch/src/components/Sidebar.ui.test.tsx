import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Sidebar } from './Sidebar';

const aiModelStatus = {
  state: 'READY' as const,
  message: 'AI 模型已就绪',
  endpoints: [],
};

function renderSidebar(currentTab = 'dashboard', onSelectTab = vi.fn(), unresolvedRiskCount = 3) {
  return render(
    <Sidebar
      currentTab={currentTab}
      onSelectTab={onSelectTab}
      unresolvedRiskCount={unresolvedRiskCount}
      aiModelStatus={aiModelStatus}
    />,
  );
}

describe('Sidebar business domain navigation', () => {
  it('renders five domain groups and the expected navigation contract', () => {
    renderSidebar();

    ['集团', '法人主体', '项目工程', '智能决策', '风险与治理'].forEach(group => {
      expect(screen.getByRole('heading', { name: group })).toBeInTheDocument();
    });

    [
      '集团经营总览',
      '法人经营画像',
      '法人法定税务',
      '项目工程库',
      'AI 税务筹划',
      'AI 审查与审单',
      '风控预警中心',
      '合规审计追溯',
    ].forEach(label => {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument();
    });

    const entityProfile = screen.getByRole('button', { name: /法人经营画像/ });
    expect(entityProfile).toBeDisabled();
    expect(entityProfile).toHaveTextContent('待接入');
    expect(screen.getByRole('button', { name: /风控预警中心/ })).toHaveTextContent('3');
  });

  it('reflects the controlled active tab without changing tab IDs', () => {
    const { rerender } = render(
      <Sidebar
        currentTab="dashboard"
        onSelectTab={vi.fn()}
        unresolvedRiskCount={0}
        aiModelStatus={aiModelStatus}
      />,
    );

    expect(screen.getByRole('button', { name: '集团经营总览' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '项目工程库' })).not.toHaveAttribute('aria-current');

    rerender(
      <Sidebar
        currentTab="projects"
        onSelectTab={vi.fn()}
        unresolvedRiskCount={0}
        aiModelStatus={aiModelStatus}
      />,
    );

    expect(screen.getByRole('button', { name: '项目工程库' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '集团经营总览' })).not.toHaveAttribute('aria-current');
  });

  it('dispatches compatible tab IDs and ignores the disabled future domain', () => {
    const onSelectTab = vi.fn();
    renderSidebar('dashboard', onSelectTab);

    fireEvent.click(screen.getByRole('button', { name: '法人法定税务' }));
    fireEvent.click(screen.getByRole('button', { name: 'AI 审查与审单' }));
    fireEvent.click(screen.getByRole('button', { name: /法人经营画像/ }));

    expect(onSelectTab).toHaveBeenNthCalledWith(1, 'tax-ledger');
    expect(onSelectTab).toHaveBeenNthCalledWith(2, 'ai-review');
    expect(onSelectTab).toHaveBeenCalledTimes(2);
  });
});
