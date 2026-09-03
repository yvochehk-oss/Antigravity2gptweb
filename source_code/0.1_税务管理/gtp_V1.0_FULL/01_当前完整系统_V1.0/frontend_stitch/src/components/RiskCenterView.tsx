import { useState } from 'react';
import {
  ShieldAlert,
  Clock,
  Sparkles,
  UserCheck,
} from 'lucide-react';
import { DataStatus, RiskEvent, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';

interface RiskCenterViewProps {
  riskEvents: RiskEvent[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onResolveRisk: (id: string) => void;
  onAskAiAboutRisk: (entityName: string) => void;
  settings?: SystemSettings;
}

export function RiskCenterView({
  riskEvents,
  dataStatus,
  dataStatusMessage,
  onResolveRisk,
  onAskAiAboutRisk,
  settings,
}: RiskCenterViewProps) {
  const [activeFilter, setActiveFilter] = useState<'全部' | '高危' | '中度' | '轻度'>('全部');

  const crossRegionThreshold = settings?.crossRegionTaxThreshold ?? 5;
  const budgetStopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;
  const filtered = riskEvents.filter(risk => activeFilter === '全部' || risk.severity === activeFilter);
  const statusMessage = dataStatusMessage.trim() || '风险集合状态暂未返回详细说明。';

  const pageTitle = (
    <header data-page-title="risk-center" className="w-full">
      <div className="flex items-start gap-2.5">
        <ShieldAlert className="mt-1 h-7 w-7 flex-shrink-0 text-[#EF4444]" />
        <div>
          <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">风控中心</h2>
          <p className="mt-1 text-[14px] text-[#c4c5d5]">
            动态监测大额跨期暂估、跨区施工预缴核销（偏差阈值 ≥{crossRegionThreshold}%）、四流一致性比对及工程造价超概算（止付阈值 ≥{budgetStopPayThreshold}%）。
          </p>
        </div>
      </div>
    </header>
  );

  if (dataStatus === 'LOADING' || dataStatus === 'UNAVAILABLE' || (dataStatus === 'DEGRADED' && riskEvents.length === 0)) {
    return (
      <div className="space-y-6">
        {pageTitle}
        <DataStatusCard
          status={dataStatus}
          title={dataStatus === 'LOADING' ? '风险集合加载中' : dataStatus === 'DEGRADED' ? '风险集合数据不完整' : '风险集合不可用'}
          message={statusMessage}
        />
      </div>
    );
  }

  if (riskEvents.length === 0) {
    return (
      <div className="space-y-6">
        {pageTitle}
        <div className="rounded-xl border border-[#10B981]/30 bg-[#10B981]/5 p-5" role="status" aria-live="polite">
          <p className="font-semibold text-[#dae2fd]">暂无已识别风险事件</p>
          <p className="mt-1 text-[13px] leading-relaxed text-[#c4c5d5]">{statusMessage}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {pageTitle}

      <div data-page-controls="risk-center" className="glass-panel flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#444653]/30 p-3.5">
        <span className="text-[12px] font-semibold text-[#c4c5d5]">风险等级筛选</span>
        <div className="flex flex-wrap items-center gap-2">
          {(['全部', '高危', '中度', '轻度'] as const).map(level => (
            <button
              key={level}
              type="button"
              onClick={() => setActiveFilter(level)}
              className={`cursor-pointer rounded-lg px-3 py-1.5 text-[12px] font-semibold transition-all ${
                activeFilter === level
                  ? level === '高危'
                    ? 'bg-[#EF4444] text-white shadow-[0_0_12px_#EF4444]'
                    : 'border border-[#4cd7f6]/40 bg-[#1e40af] text-[#dde1ff]'
                  : 'bg-[#171f33] text-[#8e909f] hover:text-[#dae2fd]'
              }`}
            >
              {level}
            </button>
          ))}
        </div>
      </div>

      {dataStatus === 'DEGRADED' && (
        <DataStatusCard status="DEGRADED" title="风险集合数据不完整" message={statusMessage} />
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {filtered.map(risk => {
          const isHigh = risk.severity === '高危';
          const isResolved = risk.status === '已闭环';
          return (
            <div
              key={risk.id}
              className={`glass-panel flex flex-col justify-between gap-4 rounded-xl p-5 transition-all ${isHigh ? 'glow-red border-[#EF4444]/60 bg-[#171f33]/90' : 'glow-amber border-[#F59E0B]/50'}`}
            >
              <div>
                <div className="mb-3 flex items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded px-2 py-0.5 text-[11px] font-bold ${isHigh ? 'animate-pulse border border-[#EF4444]/40 bg-[#EF4444]/20 text-[#ffb4ab]' : 'border border-[#F59E0B]/40 bg-[#F59E0B]/20 text-[#ffa583]'}`}>
                      {risk.severity}风险 · {risk.riskType}
                    </span>
                    <span className="flex items-center gap-1 text-[11px] font-mono-num text-[#8e909f]"><Clock className="h-3 w-3" />{risk.triggerTime}</span>
                  </div>
                  <span className="rounded border border-[#444653]/30 bg-[#131b2e] px-2.5 py-1 text-[12px] font-bold text-[#dae2fd]">{risk.status}</span>
                </div>

                <h4 className="text-[16px] font-bold text-[#dae2fd]">{risk.projectName}</h4>
                <p className="mt-0.5 text-[13px] font-medium text-[#4cd7f6]">涉税关联主体：{risk.entityName}</p>

                <div className="my-3 rounded-lg border border-[#444653]/30 bg-[#0b1326]/70 p-3 text-[12px] leading-relaxed text-[#dae2fd]">
                  <p className="mb-1 font-semibold text-[#ffa583]">风险触发诱因：</p>
                  {risk.description}
                </div>

                <div className="rounded-lg border border-[#4cd7f6]/20 bg-[#1e40af]/15 p-3 text-[12px] text-[#c4c5d5]">
                  <p className="mb-1 flex items-center gap-1 font-semibold text-[#4cd7f6]"><Sparkles className="h-3.5 w-3.5" /><span>智能审计处置建议：</span></p>
                  {risk.auditSuggestions}
                </div>
              </div>

              <div className="flex items-center justify-between border-t border-[#444653]/30 pt-3 text-[12px]">
                <div className="flex items-center gap-1.5 text-[#8e909f]"><UserCheck className="h-4 w-4 text-[#4cd7f6]" /><span>责任专员：<strong className="text-[#dae2fd]">{risk.handler}</strong></span></div>
                <div className="flex items-center gap-2">
                  <button onClick={() => onAskAiAboutRisk(risk.entityName)} className="cursor-pointer rounded-lg border border-[#4cd7f6]/40 bg-[#03b5d3]/15 px-3 py-1.5 text-[12px] font-semibold text-[#4cd7f6] hover:bg-[#03b5d3]/25">呼叫智能助手协助取证</button>
                  {!isResolved && <button onClick={() => onResolveRisk(risk.id)} className="cursor-pointer rounded-lg bg-[#10B981] px-3 py-1.5 text-[12px] font-bold text-[#0b1326] transition-colors hover:bg-[#10B981]/80">完成闭环整改</button>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
