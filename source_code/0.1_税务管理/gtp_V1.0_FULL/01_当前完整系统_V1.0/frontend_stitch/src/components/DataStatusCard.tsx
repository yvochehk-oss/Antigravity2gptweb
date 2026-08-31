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
    ? 'text-[#4cd7f6] border-[#4cd7f6]/30 bg-[#03b5d3]/5'
    : status === 'DEGRADED'
      ? 'text-[#F59E0B] border-[#F59E0B]/30 bg-[#F59E0B]/5'
      : 'text-[#ffb4ab] border-[#EF4444]/30 bg-[#EF4444]/5';

  return (
    <div className={`rounded-xl border p-5 ${tone}`} role="status" aria-live="polite">
      <div className="flex items-start gap-3">
        <Icon className={`w-5 h-5 flex-shrink-0 mt-0.5 ${isLoading ? 'animate-spin' : ''}`} />
        <div className="min-w-0">
          <p className="font-semibold text-[#dae2fd]">{title}</p>
          <p className="text-[13px] text-[#c4c5d5] mt-1 leading-relaxed">{message}</p>
          {onRetry && !isLoading && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 px-3 py-1.5 rounded-lg bg-[#1e40af] text-[#dde1ff] text-[12px] font-semibold border border-[#4cd7f6]/30 hover:bg-[#1e40af]/80"
            >
              重新加载
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ===========================================================
   保持 Windows ClearType 亚像素渲染（系统字体方案下无需 swap）
   -webkit-font-smoothing: auto  -> Windows 使用 ClearType
                                -> macOS  使用视网膜灰度平滑
   =========================================================== */
*, *::before, *::after, html, body, .antialiased {
  -webkit-font-smoothing: auto !important;
  -moz-osx-font-smoothing: auto !important;
  text-rendering: auto !important;
}
