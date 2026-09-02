import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DataStatusCard } from './DataStatusCard';

describe('DataStatusCard UI test harness', () => {
  it('renders the loading state without a retry action', () => {
    render(
      <DataStatusCard
        status="LOADING"
        title="税务数据"
        message="正在加载"
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('税务数据')).toBeInTheDocument();
    expect(screen.getByText('正在加载')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新加载' })).not.toBeInTheDocument();
  });

  it('exposes and executes retry for a degraded state', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();

    render(
      <DataStatusCard
        status="DEGRADED"
        message="数据暂时不可用"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText('数据状态')).toBeInTheDocument();
    expect(screen.getByText('数据暂时不可用')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重新加载' }));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
