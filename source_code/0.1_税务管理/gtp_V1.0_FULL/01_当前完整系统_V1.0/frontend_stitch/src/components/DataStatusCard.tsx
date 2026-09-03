import { AlertTriangle, LoaderCircle, ShieldAlert } from 'lucide-react';
import { DataStatus } from '../types';

interface DataStatusCardProps {
  status: DataStatus;
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function DataStatusCard({
  status,
  title = '数据状态',
  message,
  onRetry,
}: DataStatusCardProps) {
  const isLoading = status === 'LOADING';
  const Icon = isLoading ? LoaderCircle : status === 'DEGRADED' ? AlertTriangle : ShieldAlert;
  const tone = isLoading
    ? 'border-[var(--color-info)]/30 bg-[var(--color-info)]/5 text-[var(--color-info)]'
    : status === 'DEGRADED'
      ? 'border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 text-[var(--color-warning)]'
      : 'border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5 text-[var(--color-danger)]';

  return (
    <div className={`surface-card rounded-xl border p-5 ${tone}`} role="status" aria-live="polite">
      <div className="flex items-start gap-3">
        <Icon className={`mt-0.5 h-5 w-5 flex-shrink-0 ${isLoading ? 'animate-spin' : ''}`} />
        <div className="min-w-0">
          <p className="font-semibold text-primary">{title}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-secondary">{message}</p>
          {onRetry && !isLoading && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 rounded-lg border border-[var(--color-brand)]/30 bg-[var(--color-brand)] px-3 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-[var(--color-brand-hover)]"
            >
              重新加载
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
