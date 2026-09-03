import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Sidebar } from './Sidebar';

const aiModelStatus = {
  state: 'READY' as const,
  message: '智能模型已就绪',
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
  it('renders five domain groups with enabled entity profile and the unified intelligent decision entry', () => {
    renderSidebar();

    ['集团', '法人主体', '项目工程', '智能决策', '风险与治理'].forEach(group => {
      expect(screen.getByRole('heading', { name: group })).toBeInTheDocument();
    });

    [
      '集团经营总览',
      '法人经营画像',
      '法人法定税务',
      '项目工程库',
      '智能财税决策中心',
      '风控中心',
      '合规审计',
    ].forEach(label => {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument();
    });

    expect(screen.queryByRole('button', { name: /智能税务筹划/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /智能审查与审单/ })).not.toBeInTheDocument();

    const entityProfile = screen.getByRole('button', { name: '法人经营画像' });
    expect(entityProfile).toBeEnabled();
    expect(entityProfile).not.toHaveTextContent('待接入');
    expect(screen.getByRole('button', { name: /风控中心/ })).toHaveTextContent('3');
  });

  it('reflects the controlled active tab including entity-profile and intelligent-decision', () => {
    const onSelectTab = vi.fn();
    const { rerender } = render(
      <Sidebar currentTab="dashboard" onSelectTab={onSelectTab} unresolvedRiskCount={0} aiModelStatus={aiModelStatus} />,
    );

    expect(screen.getByRole('button', { name: '集团经营总览' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '法人经营画像' })).not.toHaveAttribute('aria-current');

    rerender(<Sidebar currentTab="entity-profile" onSelectTab={onSelectTab} unresolvedRiskCount={0} aiModelStatus={aiModelStatus} />);
    expect(screen.getByRole('button', { name: '法人经营画像' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '集团经营总览' })).not.toHaveAttribute('aria-current');

    rerender(<Sidebar currentTab="ai-decision" onSelectTab={onSelectTab} unresolvedRiskCount={0} aiModelStatus={aiModelStatus} />);
    expect(screen.getByRole('button', { name: '智能财税决策中心' })).toHaveAttribute('aria-current', 'page');
  });

  it('dispatches entity-profile, statutory tax, and intelligent-decision navigation', () => {
    const onSelectTab = vi.fn();
    renderSidebar('dashboard', onSelectTab);

    fireEvent.click(screen.getByRole('button', { name: '法人经营画像' }));
    fireEvent.click(screen.getByRole('button', { name: '法人法定税务' }));
    fireEvent.click(screen.getByRole('button', { name: '智能财税决策中心' }));

    expect(onSelectTab).toHaveBeenNthCalledWith(1, 'entity-profile');
    expect(onSelectTab).toHaveBeenNthCalledWith(2, 'tax-ledger');
    expect(onSelectTab).toHaveBeenNthCalledWith(3, 'ai-decision');
    expect(onSelectTab).toHaveBeenCalledTimes(3);
  });
});
