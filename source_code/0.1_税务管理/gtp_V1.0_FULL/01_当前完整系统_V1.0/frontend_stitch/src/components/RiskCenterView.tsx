import { useState } from 'react';
import { ShieldAlert, Clock, Sparkles, UserCheck } from 'lucide-react';
import { postJson } from '../api';
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

interface RiskDispositionResponse {
  status: 'success';
  risk_id: number;
  resolved: boolean;
  display_status: string;
}

export function RiskCenterView({
  riskEvents,
  dataStatus,
  dataStatusMessage,
  onAskAiAboutRisk,
  settings,
}: RiskCenterViewProps) {
  const [activeFilter, setActiveFilter] = useState<'全部' | '高危' | '中度' | '轻度'>('全部');
  const [resolvedIds, setResolvedIds] = useState<Set<string>>(() => new Set());
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set());
  const [actionError, setActionError] = useState('');

  const crossRegionThreshold = settings?.crossRegionTaxThreshold ?? 5;
  const budgetStopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;
  const filtered = riskEvents.filter(risk => activeFilter === '全部' || risk.severity === activeFilter);
  const statusMessage = dataStatusMessage.trim() || '风险集合状态暂未返回详细说明。';

  const persistResolution = async (risk: RiskEvent) => {
    if (pendingIds.has(risk.id)) return;
    const numericId = Number(risk.id);
    if (!Number.isInteger(numericId) || numericId <= 0) {
      setActionError('风险事件 ID 无效，无法提交处置。');
      return;
    }
    setActionError('');
    setPendingIds(previous => new Set(previous).add(risk.id));
    try {
      const result = await postJson<RiskDispositionResponse>(
        '/api/v1/risk/disposition',
        {
          risk_id: numericId,
          resolved: true,
          handler: risk.handler || '',
          note: '前端风控中心完成闭环整改',
        },
      );
      if (result.status !== 'success' || result.resolved !== true || result.risk_id !== numericId) {
        throw new Error('风险处置接口返回状态不完整。');
      }
      setResolvedIds(previous => new Set(previous).add(risk.id));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '风险处置提交失败，请稍后重试。');
    } finally {
      setPendingIds(previous => {
        const next = new Set(previous);
        next.delete(risk.id);
        return next;
      });
    }
  };

  const pageTitle = (
    <header data-page-title="risk-center" className="w-full">
      <div className="flex items-start gap-2.5">
        <ShieldAlert className="mt-1 h-7 w-7 flex-shrink-0 text-[var(--color-danger)]" />
        <div>
          <h2 className="text-[28px] font-bold tracking-tight text-[var(--color-text-primary)]">风控中心</h2>
          <p className="mt-1 text-[14px] text-[var(--color-text-secondary)]">
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
        <div className="surface-card rounded-xl border border-[var(--color-success)]/30 p-5" role="status">
          <p className="font-semibold text-[var(--color-text-primary)]">暂无已识别风险事件</p>
          <p className="mt-1 text-[13px] text-[var(--color-text-secondary)]">{statusMessage}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {pageTitle}
      <div data-page-controls="risk-center" className="surface-card flex flex-wrap items-center justify-between gap-3 rounded-xl p-3.5">
        <span className="text-[12px] font-semibold text-[var(--color-text-secondary)]">风险等级筛选</span>
        <div className="flex flex-wrap items-center gap-2">
          {(['全部', '高危', '中度', '轻度'] as const).map(level => (
            <button
              key={level}
              type="button"
              onClick={() => setActiveFilter(level)}
              className={`cursor-pointer rounded-lg px-3 py-1.5 text-[12px] font-semibold ${activeFilter === level ? 'border border-[var(--color-brand)]/40 bg-[var(--color-brand-muted)] text-[var(--color-brand)]' : 'bg-[var(--color-surface-2)] text-[var(--color-text-secondary)]'}`}
            >
              {level}
            </button>
          ))}
        </div>
      </div>

      {dataStatus === 'DEGRADED' && <DataStatusCard status="DEGRADED" title="风险集合数据不完整" message={statusMessage} />}
      {actionError && (
        <div className="rounded-xl border border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5 p-3 text-[13px] text-[var(--color-danger)]" role="alert">
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {filtered.map(risk => {
          const isHigh = risk.severity === '高危';
          const isResolved = risk.status === '已闭环' || resolvedIds.has(risk.id);
          const isPending = pendingIds.has(risk.id);
          return (
            <div key={risk.id} className="surface-card flex flex-col justify-between gap-4 rounded-xl p-5">
              <div>
                <div className="mb-3 flex items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${isHigh ? 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/15 text-[var(--color-danger)]' : 'border-[var(--color-warning)]/40 bg-[var(--color-warning)]/15 text-[var(--color-warning)]'}`}>
                      {risk.severity}风险 · {risk.riskType}
                    </span>
                    <span className="flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]"><Clock className="h-3 w-3" />{risk.triggerTime}</span>
                  </div>
                  <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2.5 py-1 text-[12px] font-bold text-[var(--color-text-primary)]">{isResolved ? '已闭环' : risk.status}</span>
                </div>
                <h4 className="text-[16px] font-bold text-[var(--color-text-primary)]">{risk.projectName}</h4>
                <p className="mt-0.5 text-[13px] font-medium text-[var(--color-brand)]">涉税关联主体：{risk.entityName}</p>
                <div className="my-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-3 text-[12px] leading-relaxed text-[var(--color-text-primary)]">
                  <p className={`mb-1 font-semibold ${isHigh ? 'text-[var(--color-danger)]' : 'text-[var(--color-warning)]'}`}>风险触发诱因：</p>
                  {risk.description}
                </div>
                <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-3 text-[12px] text-[var(--color-text-secondary)]">
                  <p className="mb-1 flex items-center gap-1 font-semibold text-[var(--color-brand)]"><Sparkles className="h-3.5 w-3.5" />智能审计处置建议：</p>
                  {risk.auditSuggestions}
                </div>
              </div>
              <div className="flex items-center justify-between border-t border-[var(--color-border)] pt-3 text-[12px]">
                <div className="flex items-center gap-1.5 text-[var(--color-text-muted)]"><UserCheck className="h-4 w-4 text-[var(--color-brand)]" /><span>责任专员：<strong className="text-[var(--color-text-primary)]">{risk.handler}</strong></span></div>
                <div className="flex items-center gap-2">
                  <button onClick={() => onAskAiAboutRisk(risk.entityName)} className="rounded-lg border border-[var(--color-brand)]/40 bg-[var(--color-brand-muted)] px-3 py-1.5 font-semibold text-[var(--color-brand)]">呼叫智能助手协助取证</button>
                  {!isResolved && (
                    <button
                      onClick={() => void persistResolution(risk)}
                      disabled={isPending}
                      className="rounded-lg border border-[var(--color-success)]/40 bg-[var(--color-success)]/15 px-3 py-1.5 font-bold text-[var(--color-success)] disabled:opacity-50"
                    >
                      {isPending ? '提交中…' : '完成闭环整改'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
