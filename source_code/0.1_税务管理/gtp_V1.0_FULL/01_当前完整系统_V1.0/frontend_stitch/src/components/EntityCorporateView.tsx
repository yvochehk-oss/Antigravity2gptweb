import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Building2, Landmark, ReceiptText } from 'lucide-react';
import * as api from '../api';
import type { LegalEntityOperatingProjection, LegalEntityProjectionContribution } from '../api';
import {
  fetchLegalEntities,
  fetchLegalEntityFactPeriods,
  fetchLegalEntityStatutoryVat,
  type LegalEntityFactPeriod,
  type LegalEntityMasterData,
} from '../legalEntityApi';
import type { DataStatus, EntityTaxLedgerRecord } from '../types';
import { dataStatusLabel } from './uiLocalization';

const currentYearNum = new Date().getFullYear();
export const YEAR_OPTIONS: string[] = [];
for (let y = 2023; y <= Math.max(2028, currentYearNum + 2); y += 1) YEAR_OPTIONS.push(String(y));

export const MONTH_OPTIONS = Array.from({ length: 12 }, (_, index) => {
  const value = String(index + 1).padStart(2, '0');
  return { value, label: `${value}月` };
});

function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

function factPeriodLabel(item: LegalEntityFactPeriod): string {
  const [year, month] = item.period.split('-');
  return `${year}年${month}月 · ${item.factCount}笔${item.isPrimary ? '（主力数据）' : ''}`;
}

function translateDataGap(gap: string): string {
  switch (gap) {
    case 'INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW':
      return '进项税额合规性待确认（发票进项需经税务抵扣核准，目前记入“待确认进项税额”审慎管理，未冒进计入可抵扣）';
    case 'INPUT_VAT_ACCOUNTING_IDENTITY_FAILED':
      return '进项税额勾稽平衡校验异常';
    case 'CANONICAL_INVOICE_PERIOD_MISSING':
      return '部分规范发票事实缺少所属期间标记';
    default:
      if (gap.startsWith('ENTITY_NOT_IN_INTERNAL_SCOPE:')) return `主体 ${gap.split(':')[1]} 不在系统内部合并范围内`;
      return '存在尚未完成中文归类的数据缺口，请复核后端明细。';
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '税务服务暂时不可用。';
}

function StatusLine({ status, message }: { status: DataStatus; message: string }) {
  if (status === 'READY' || !message) return null;
  return (
    <div role="status" className={`rounded-lg border px-3 py-2 text-[12px] ${status === 'UNAVAILABLE' ? 'border-[#EF4444]/30 bg-[#EF4444]/5 text-[#ffb4b4]' : 'border-[#F59E0B]/30 bg-[#F59E0B]/5 text-[#ffd0a8]'}`}>
      {dataStatusLabel(status)} · {message}
    </div>
  );
}

function ProjectionKpi({ label, value }: { label: string; value: number | undefined }) {
  return <div className="glass-panel rounded-xl border border-[#444653]/25 p-3.5"><div className="text-[11px] text-[#8e909f]">{label}</div><div className="mt-1 text-[17px] font-bold text-[#dae2fd] font-mono-num">{formatAmount(value)}</div></div>;
}

function StatutoryKpi({ label, value }: { label: string; value: number | undefined }) {
  return <div className="rounded-xl border border-[#4cd7f6]/20 bg-[#03b5d3]/5 p-3.5"><div className="text-[11px] text-[#8e909f]">{label}</div><div className="mt-1 text-[17px] font-bold text-[#dde1ff] font-mono-num">{formatAmount(value)}</div></div>;
}

function ContributionRow({ item }: { item: LegalEntityProjectionContribution }) {
  return (
    <tr className="border-t border-[#444653]/20">
      <td className="px-3 py-2.5"><div className="font-semibold text-[#dae2fd]">{item.projectName || '非项目归属'}</div><div className="text-[11px] text-[#8e909f]">{item.projectCode || '—'}</div></td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.revenue)}</td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.bookCostProjection)}</td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.outputVat)}</td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.inputVat)}</td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.internalTradeNet)}</td>
      <td className="px-3 py-2.5 text-right font-mono-num">{formatAmount(item.internalTradeVat)}</td>
    </tr>
  );
}

export function EntityCorporateView() {
  const [legalEntities, setLegalEntities] = useState<LegalEntityMasterData[]>([]);
  const [masterStatus, setMasterStatus] = useState<DataStatus>('LOADING');
  const [masterMessage, setMasterMessage] = useState('正在读取法人主数据…');
  const [entityCode, setEntityCode] = useState('');
  const [period, setPeriod] = useState(currentPeriod);
  const [selectedYear, selectedMonth] = useMemo(() => {
    const parts = period.split('-');
    return [parts[0] || String(currentYearNum), parts[1] || '01'];
  }, [period]);

  const [factPeriods, setFactPeriods] = useState<LegalEntityFactPeriod[]>([]);
  const [factPeriodStatus, setFactPeriodStatus] = useState<'LOADING' | 'READY' | 'EMPTY' | 'UNAVAILABLE'>('LOADING');
  const [factPeriodMessage, setFactPeriodMessage] = useState('等待法人主数据…');

  const [projection, setProjection] = useState<LegalEntityOperatingProjection | null>(null);
  const [projectionStatus, setProjectionStatus] = useState<DataStatus>('LOADING');
  const [projectionMessage, setProjectionMessage] = useState('等待法人主数据…');
  const [statutoryRecords, setStatutoryRecords] = useState<EntityTaxLedgerRecord[]>([]);
  const [statutoryStatus, setStatutoryStatus] = useState<DataStatus>('LOADING');
  const [statutoryMessage, setStatutoryMessage] = useState('等待法人主数据…');

  const masterAbortRef = useRef<AbortController | null>(null);
  const factPeriodsAbortRef = useRef<AbortController | null>(null);
  const factPeriodEntityRef = useRef<string | null>(null);
  const projectionAbortRef = useRef<AbortController | null>(null);
  const statutoryAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    masterAbortRef.current = controller;
    setMasterStatus('LOADING');
    setMasterMessage('正在读取法人主数据…');
    void fetchLegalEntities(controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setLegalEntities(result.items);
        setMasterStatus('READY');
        setMasterMessage('');
        setEntityCode(current => {
          if (result.items.some(item => item.canonicalCode === current)) return current;
          const preferred = result.items.find(item => item.canonicalCode === 'A08');
          return preferred?.canonicalCode ?? result.items[0]?.canonicalCode ?? '';
        });
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setLegalEntities([]);
        setEntityCode('');
        setMasterStatus('UNAVAILABLE');
        setMasterMessage(errorMessage(error));
      })
      .finally(() => {
        if (masterAbortRef.current === controller) masterAbortRef.current = null;
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    factPeriodsAbortRef.current?.abort();
    if (!entityCode) {
      setFactPeriods([]);
      setFactPeriodStatus('LOADING');
      setFactPeriodMessage('等待法人主数据…');
      return;
    }
    const previousEntityCode = factPeriodEntityRef.current;
    factPeriodEntityRef.current = entityCode;
    const controller = new AbortController();
    factPeriodsAbortRef.current = controller;
    setFactPeriods([]);
    setFactPeriodStatus('LOADING');
    setFactPeriodMessage('正在统计规范发票事实期间…');
    void fetchLegalEntityFactPeriods(entityCode, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setFactPeriods(result.periods.slice(0, 4));
        setFactPeriodStatus(result.status);
        if (result.status === 'EMPTY') {
          setFactPeriodMessage('当前法人暂无带有效年月期间的规范发票事实。');
        } else if (result.unperiodizedFactCount > 0) {
          setFactPeriodMessage(`另有 ${result.unperiodizedFactCount} 条规范发票事实缺少有效期间，未纳入推荐。`);
        } else {
          setFactPeriodMessage('');
        }
        if (previousEntityCode && previousEntityCode !== entityCode) {
          const primary = result.periods.find(item => item.isPrimary) ?? result.periods[0];
          if (primary) setPeriod(primary.period);
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setFactPeriods([]);
        setFactPeriodStatus('UNAVAILABLE');
        setFactPeriodMessage(errorMessage(error));
      })
      .finally(() => {
        if (factPeriodsAbortRef.current === controller) factPeriodsAbortRef.current = null;
      });
    return () => controller.abort();
  }, [entityCode]);

  useEffect(() => {
    projectionAbortRef.current?.abort();
    if (!entityCode) {
      setProjection(null);
      setProjectionStatus('LOADING');
      setProjectionMessage('等待法人主数据…');
      return;
    }
    const controller = new AbortController();
    projectionAbortRef.current = controller;
    setProjection(null);
    setProjectionStatus('LOADING');
    setProjectionMessage('正在读取法人经营投影…');
    void api.fetchLegalEntityOperatingProjection(entityCode, period, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setProjection(result);
        setProjectionStatus(result.status);
        setProjectionMessage(result.status === 'DEGRADED' ? `经营投影存在数据缺口：${result.dataGaps.map(translateDataGap).join('；') || '后端标记为降级运行'}` : '');
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setProjectionStatus('UNAVAILABLE');
        setProjectionMessage(errorMessage(error));
      })
      .finally(() => {
        if (projectionAbortRef.current === controller) projectionAbortRef.current = null;
      });
    return () => controller.abort();
  }, [entityCode, period]);

  useEffect(() => {
    statutoryAbortRef.current?.abort();
    if (!entityCode) {
      setStatutoryRecords([]);
      setStatutoryStatus('LOADING');
      setStatutoryMessage('等待法人主数据…');
      return;
    }
    const controller = new AbortController();
    statutoryAbortRef.current = controller;
    setStatutoryRecords([]);
    setStatutoryStatus('LOADING');
    setStatutoryMessage('正在读取法人法定申报增值税台账…');
    const entityName = legalEntities.find(item => item.canonicalCode === entityCode)?.legalName;
    void fetchLegalEntityStatutoryVat(controller.signal, entityCode, period, entityName)
      .then(result => {
        if (controller.signal.aborted) return;
        const selected = result.items.filter(record => record.entityCode === entityCode && record.period === period);
        setStatutoryRecords(selected);
        setStatutoryStatus(result.status);
        setStatutoryMessage(result.status === 'DEGRADED' ? result.message || '法人法定申报增值税台账由后端标记为降级运行，请复核。' : selected.length === 0 ? '该法人当前期间暂无已生成的法定申报增值税台账。' : '');
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setStatutoryStatus('UNAVAILABLE');
        setStatutoryMessage(errorMessage(error));
      })
      .finally(() => {
        if (statutoryAbortRef.current === controller) statutoryAbortRef.current = null;
      });
    return () => controller.abort();
  }, [entityCode, period, legalEntities]);

  useEffect(() => () => {
    masterAbortRef.current?.abort();
    factPeriodsAbortRef.current?.abort();
    projectionAbortRef.current?.abort();
    statutoryAbortRef.current?.abort();
  }, []);

  const statutoryRecord = statutoryRecords.length === 1 ? statutoryRecords[0] : null;
  const activeEntity = useMemo(() => legalEntities.find(item => item.canonicalCode === entityCode), [legalEntities, entityCode]);
  const activeEntityName = activeEntity?.legalName || entityCode || '未选择法人';
  const contributionRows = useMemo(() => {
    if (!projection) return [];
    const rows = [...projection.projectContributions];
    if (projection.nonProjectContribution.factCount > 0) rows.push(projection.nonProjectContribution);
    return rows;
  }, [projection]);

  return (
    <div className="space-y-6">
      <header data-page-title="entity-profile" className="w-full">
        <div className="flex items-start gap-2.5">
          <Landmark className="mt-1 h-7 w-7 flex-shrink-0 text-[#4cd7f6]" />
          <div>
            <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">法人经营画像</h2>
            <p className="mt-1 text-[13px] text-[#c4c5d5]">【{entityCode || '—'} · {activeEntityName}】经营投影与法定申报增值税双域并列展示，禁止将管理投影冒充法人申报结果。</p>
          </div>
        </div>
      </header>

      <div data-page-controls="entity-profile" className="glass-panel flex flex-wrap items-end gap-3 rounded-xl border border-[#444653]/30 bg-[#171f33]/50 p-3.5">
        <label className="flex min-w-[220px] max-w-[320px] flex-col gap-1 text-[11px] text-[#8e909f]">
          法人主体
          <select aria-label="法人主体" value={entityCode} disabled={masterStatus !== 'READY' || legalEntities.length === 0} onChange={event => setEntityCode(event.target.value)} className="truncate rounded-lg border border-[#444653]/40 bg-[#0b1326] px-3 py-2 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none">
            {legalEntities.length === 0 && <option value="">正在加载法人主数据…</option>}
            {legalEntities.map(item => <option key={item.partyId} value={item.canonicalCode}>{item.canonicalCode} · {item.legalName}</option>)}
          </select>
        </label>
        <div className="flex flex-col gap-1 text-[11px] text-[#8e909f]">
          <span>所属期间</span>
          <div className="flex items-center gap-2">
            <select aria-label="所属年份" value={selectedYear} onChange={event => setPeriod(`${event.target.value}-${selectedMonth}`)} className="rounded-lg border border-[#444653]/40 bg-[#0b1326] px-2.5 py-2 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none">{YEAR_OPTIONS.map(year => <option key={year} value={year}>{year}年</option>)}</select>
            <select aria-label="所属月份" value={selectedMonth} onChange={event => setPeriod(`${selectedYear}-${event.target.value}`)} className="rounded-lg border border-[#444653]/40 bg-[#0b1326] px-2.5 py-2 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none">{MONTH_OPTIONS.map(month => <option key={month.value} value={month.value}>{month.label}</option>)}</select>
          </div>
        </div>
        <div className="flex min-w-[300px] flex-1 flex-col gap-1 text-[11px] text-[#8e909f]">
          <span>含数据事实期间推荐</span>
          {factPeriodStatus === 'LOADING' && <span className="py-1 text-[#8e909f]">正在统计规范发票事实期间…</span>}
          {factPeriodStatus === 'UNAVAILABLE' && <span className="py-1 text-[#ffb4b4]">服务不可用 · {factPeriodMessage}</span>}
          {factPeriodStatus === 'EMPTY' && <span className="py-1 text-[#8e909f]">{factPeriodMessage}</span>}
          {factPeriodStatus === 'READY' && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                {factPeriods.map(item => (
                  <button key={item.period} type="button" onClick={() => setPeriod(item.period)} className={`rounded border px-2 py-1 text-[11px] transition-colors ${period === item.period ? 'border-[#4cd7f6] bg-[#4cd7f6]/20 font-semibold text-[#4cd7f6]' : 'border-[#444653]/40 bg-[#0b1326] text-[#c4c5d5] hover:border-[#4cd7f6]/50'}`}>{factPeriodLabel(item)}</button>
                ))}
              </div>
              {factPeriodMessage && <div className="text-[10px] text-[#F59E0B]">{factPeriodMessage}</div>}
            </div>
          )}
        </div>
      </div>

      <StatusLine status={masterStatus} message={masterMessage} />

      <section aria-label="经营投影" className="space-y-4 rounded-2xl border border-[#8b5cf6]/30 bg-[#8b5cf6]/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-[15px] font-bold text-[#ddd6fe]"><Building2 className="h-4 w-4" />经营投影</div><p className="mt-1 text-[12px] font-semibold text-[#c4b5fd]">法人管理／经营投影 · 管理口径，非申报口径</p></div><span className="rounded-full border border-[#8b5cf6]/30 px-2.5 py-1 text-[10px] text-[#c4b5fd]"><span>{entityCode || '—'} · {period}</span>{activeEntityName && activeEntityName !== entityCode && <span className="ml-1 text-[#e9d5ff]">（{activeEntityName}）</span>}</span></div>
        <StatusLine status={projectionStatus} message={projectionMessage} />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5"><ProjectionKpi label="收入投影" value={projection?.revenue} /><ProjectionKpi label="账面成本投影" value={projection?.bookCostProjection} /><ProjectionKpi label="会计利润投影" value={projection?.accountingProfitProjection} /><ProjectionKpi label="销项增值税投影" value={projection?.outputVat} /><ProjectionKpi label="进项增值税投影" value={projection?.inputVat} /><ProjectionKpi label="可抵扣进项税额" value={projection?.deductibleInputVat} /><ProjectionKpi label="不可抵扣进项税额" value={projection?.nondeductibleInputVat} /><ProjectionKpi label="待确认进项税额" value={projection?.pendingInputVat} /><ProjectionKpi label="内部法人交易净额" value={projection?.internalTradeNet} /><ProjectionKpi label="内部法人交易增值税" value={projection?.internalTradeVat} /></div>
        <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]" role="note">企业所得税／税后利润：暂未接入（等待确定性企业所得税引擎接入）</div>
      </section>

      <section aria-label="法定申报增值税" className="space-y-4 rounded-2xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-[15px] font-bold text-[#4cd7f6]"><ReceiptText className="h-4 w-4" />法定申报增值税</div><p className="mt-1 text-[12px] font-semibold text-[#4cd7f6]">法人法定申报 · 法人申报口径</p></div><span className="rounded-full border border-[#4cd7f6]/30 px-2.5 py-1 text-[10px] text-[#4cd7f6]">权威来源：法人增值税法定台账</span></div>
        <StatusLine status={statutoryStatus} message={statutoryMessage} />
        {statutoryRecords.length > 1 && <div role="status" className="flex items-center gap-2 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]"><AlertTriangle className="h-4 w-4" />降级运行 · 同一法人／期间返回多条法定申报增值税当前记录，未在前端合并，请复核后端当前台账唯一性约束。</div>}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6"><StatutoryKpi label="期初留抵" value={statutoryRecord?.openingInputCredit} /><StatutoryKpi label="销项税额" value={statutoryRecord?.outputVat} /><StatutoryKpi label="进项税额" value={statutoryRecord?.inputVat} /><StatutoryKpi label="税款预缴" value={statutoryRecord?.taxPrepayment} /><StatutoryKpi label="实际应纳增值税" value={statutoryRecord?.vatPayableAfterPrepayment} /><StatutoryKpi label="期末留抵" value={statutoryRecord?.closingInputCredit} /></div>
        {statutoryStatus === 'READY' && statutoryRecords.length === 0 && <div className="rounded-xl border border-[#444653]/30 bg-[#171f33]/60 p-3 text-[12px] leading-relaxed text-[#8e909f]"><span className="font-semibold text-[#dae2fd]">提示：</span>上方【法定申报增值税】是纳税申报口径，由财务核对进销凭证后在【法人法定税务】模块执行“受控确定性生成”后正式入账。当前期间未归档正式台账，故显示为“—”，并不影响上方的经营投影与项目穿透分析。</div>}
      </section>

      <section aria-label="项目穿透贡献" className="space-y-3 rounded-2xl border border-[#444653]/30 bg-[#171f33]/40 p-4">
        <div><h3 className="text-[15px] font-bold text-[#dae2fd]">项目穿透贡献</h3><p className="mt-1 text-[12px] text-[#8e909f]">以下仅为法人管理／经营投影中的项目贡献拆分，不改变项目事实或法人法定申报增值税。</p></div>
        <div className="overflow-x-auto rounded-xl border border-[#444653]/30"><table className="w-full min-w-[860px] text-[12px] text-[#c4c5d5]"><thead className="bg-[#0b1326]/70 text-[#8e909f]"><tr><th className="px-3 py-2.5 text-left">项目</th><th className="px-3 py-2.5 text-right">收入贡献</th><th className="px-3 py-2.5 text-right">成本投影</th><th className="px-3 py-2.5 text-right">销项税额投影</th><th className="px-3 py-2.5 text-right">进项税额投影</th><th className="px-3 py-2.5 text-right">内部交易净额</th><th className="px-3 py-2.5 text-right">内部交易增值税</th></tr></thead><tbody>{contributionRows.length === 0 ? <tr><td colSpan={7} className="px-3 py-8 text-center text-[#8e909f]">当前选择暂无项目贡献投影。</td></tr> : contributionRows.map((item, index) => <ContributionRow key={`${item.projectId ?? 'non-project'}-${index}`} item={item} />)}</tbody></table></div>
      </section>
    </div>
  );
}
