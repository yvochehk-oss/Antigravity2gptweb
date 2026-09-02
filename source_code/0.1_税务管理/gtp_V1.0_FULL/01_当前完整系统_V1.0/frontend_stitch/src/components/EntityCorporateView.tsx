import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Building2, Landmark, ReceiptText } from 'lucide-react';
import * as api from '../api';
import type { LegalEntityOperatingProjection, LegalEntityProjectionContribution } from '../api';
import type { DataStatus, EntityTaxLedgerRecord } from '../types';

const ENTITY_OPTIONS = ['A08', 'B01', 'B02', 'B03', 'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07'];

function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Tax 服务暂时不可用。';
}

function StatusLine({ status, message }: { status: DataStatus; message: string }) {
  if (status === 'READY' || !message) return null;
  const prefix = status === 'LOADING' ? 'LOADING' : status === 'DEGRADED' ? 'DEGRADED' : 'UNAVAILABLE';
  return (
    <div
      role="status"
      className={`rounded-lg border px-3 py-2 text-[12px] ${
        status === 'UNAVAILABLE'
          ? 'border-[#EF4444]/30 bg-[#EF4444]/5 text-[#ffb4b4]'
          : 'border-[#F59E0B]/30 bg-[#F59E0B]/5 text-[#ffd0a8]'
      }`}
    >
      {prefix} · {message}
    </div>
  );
}

function ProjectionKpi({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="glass-panel rounded-xl border border-[#444653]/25 p-3.5">
      <div className="text-[11px] text-[#8e909f]">{label}</div>
      <div className="mt-1 text-[17px] font-bold text-[#dae2fd] font-mono-num">{formatAmount(value)}</div>
    </div>
  );
}

function StatutoryKpi({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="rounded-xl border border-[#4cd7f6]/20 bg-[#03b5d3]/5 p-3.5">
      <div className="text-[11px] text-[#8e909f]">{label}</div>
      <div className="mt-1 text-[17px] font-bold text-[#dde1ff] font-mono-num">{formatAmount(value)}</div>
    </div>
  );
}

function ContributionRow({ item }: { item: LegalEntityProjectionContribution }) {
  return (
    <tr className="border-t border-[#444653]/20">
      <td className="px-3 py-2.5">
        <div className="font-semibold text-[#dae2fd]">{item.projectName || '非项目归属'}</div>
        <div className="text-[11px] text-[#8e909f]">{item.projectCode || '—'}</div>
      </td>
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
  const [entityCode, setEntityCode] = useState('A08');
  const [period, setPeriod] = useState(currentPeriod);
  const [projection, setProjection] = useState<LegalEntityOperatingProjection | null>(null);
  const [projectionStatus, setProjectionStatus] = useState<DataStatus>('LOADING');
  const [projectionMessage, setProjectionMessage] = useState('正在读取法人经营 Projection…');
  const [statutoryRecords, setStatutoryRecords] = useState<EntityTaxLedgerRecord[]>([]);
  const [statutoryStatus, setStatutoryStatus] = useState<DataStatus>('LOADING');
  const [statutoryMessage, setStatutoryMessage] = useState('正在读取法人正式 VAT 台账…');

  const projectionAbortRef = useRef<AbortController | null>(null);
  const statutoryAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    projectionAbortRef.current?.abort();
    const controller = new AbortController();
    projectionAbortRef.current = controller;
    setProjection(null);
    setProjectionStatus('LOADING');
    setProjectionMessage('正在读取法人经营 Projection…');

    void api.fetchLegalEntityOperatingProjection(entityCode, period, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setProjection(result);
        setProjectionStatus(result.status);
        setProjectionMessage(
          result.status === 'DEGRADED'
            ? `Projection 存在数据缺口：${result.dataGaps.join('、') || '后端标记为 DEGRADED'}`
            : '',
        );
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
    const controller = new AbortController();
    statutoryAbortRef.current = controller;
    setStatutoryRecords([]);
    setStatutoryStatus('LOADING');
    setStatutoryMessage('正在读取法人正式 VAT 台账…');

    void api.fetchEntityTaxLedger(controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        const selected = result.items.filter(
          record => record.entityCode === entityCode && record.period === period,
        );
        setStatutoryRecords(selected);
        setStatutoryStatus(result.status);
        setStatutoryMessage(
          result.status === 'DEGRADED'
            ? result.message || '法人正式 VAT 台账由后端标记为 DEGRADED，请复核。'
            : selected.length === 0
              ? '该法人当前期间暂无已生成的正式 VAT 台账。'
              : '',
        );
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
  }, [entityCode, period]);

  useEffect(() => () => {
    projectionAbortRef.current?.abort();
    statutoryAbortRef.current?.abort();
  }, []);

  const statutoryRecord = statutoryRecords.length === 1 ? statutoryRecords[0] : null;
  const contributionRows = useMemo(() => {
    if (!projection) return [];
    const rows = [...projection.projectContributions];
    if (projection.nonProjectContribution.factCount > 0) rows.push(projection.nonProjectContribution);
    return rows;
  }, [projection]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Landmark className="h-6 w-6 text-[#4cd7f6]" />
            <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">法人经营画像</h2>
          </div>
          <p className="mt-1 text-[13px] text-[#c4c5d5]">经营 Projection 与正式 VAT 双域并列展示，禁止将管理投影冒充法人申报结果。</p>
        </div>
        <div className="flex flex-wrap items-end gap-3 rounded-xl border border-[#444653]/30 bg-[#171f33]/50 p-3">
          <label className="flex min-w-[160px] flex-col gap-1 text-[11px] text-[#8e909f]">
            法人主体
            <select
              aria-label="法人主体"
              value={entityCode}
              onChange={event => setEntityCode(event.target.value)}
              className="rounded-lg border border-[#444653]/40 bg-[#0b1326] px-3 py-2 text-[13px] text-[#dae2fd]"
            >
              {ENTITY_OPTIONS.map(code => <option key={code} value={code}>{code}</option>)}
            </select>
          </label>
          <label className="flex min-w-[160px] flex-col gap-1 text-[11px] text-[#8e909f]">
            期间
            <input
              aria-label="期间"
              type="month"
              value={period}
              onChange={event => setPeriod(event.target.value)}
              className="rounded-lg border border-[#444653]/40 bg-[#0b1326] px-3 py-2 text-[13px] text-[#dae2fd]"
            />
          </label>
        </div>
      </div>

      <section aria-label="经营 Projection" className="space-y-4 rounded-2xl border border-[#8b5cf6]/30 bg-[#8b5cf6]/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[15px] font-bold text-[#ddd6fe]">
              <Building2 className="h-4 w-4" />
              经营 Projection
            </div>
            <p className="mt-1 text-[12px] font-semibold text-[#c4b5fd]">LEGAL_ENTITY_PROJECTION · 管理/经营投影，非申报口径</p>
          </div>
          <span className="rounded-full border border-[#8b5cf6]/30 px-2.5 py-1 text-[10px] text-[#c4b5fd]">{entityCode} · {period}</span>
        </div>

        <StatusLine status={projectionStatus} message={projectionMessage} />

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <ProjectionKpi label="收入投影" value={projection?.revenue} />
          <ProjectionKpi label="账面成本投影" value={projection?.bookCostProjection} />
          <ProjectionKpi label="会计利润投影" value={projection?.accountingProfitProjection} />
          <ProjectionKpi label="销项 VAT Projection" value={projection?.outputVat} />
          <ProjectionKpi label="进项 VAT Projection" value={projection?.inputVat} />
          <ProjectionKpi label="可抵扣进项 VAT" value={projection?.deductibleInputVat} />
          <ProjectionKpi label="不可抵扣进项 VAT" value={projection?.nondeductibleInputVat} />
          <ProjectionKpi label="待确认进项 VAT" value={projection?.pendingInputVat} />
          <ProjectionKpi label="内部法人交易净额" value={projection?.internalTradeNet} />
          <ProjectionKpi label="内部法人交易 VAT" value={projection?.internalTradeVat} />
        </div>

        <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]" role="note">
          CIT / 税后利润：NOT AVAILABLE（等待确定性 CIT 引擎接入）
        </div>
      </section>

      <section aria-label="正式 VAT" className="space-y-4 rounded-2xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[15px] font-bold text-[#4cd7f6]">
              <ReceiptText className="h-4 w-4" />
              正式 VAT
            </div>
            <p className="mt-1 text-[12px] font-semibold text-[#4cd7f6]">LEGAL_ENTITY_STATUTORY · 法人申报口径</p>
          </div>
          <span className="rounded-full border border-[#4cd7f6]/30 px-2.5 py-1 text-[10px] text-[#4cd7f6]">source: entity_vat_ledgers</span>
        </div>

        <StatusLine status={statutoryStatus} message={statutoryMessage} />
        {statutoryRecords.length > 1 && (
          <div role="status" className="flex items-center gap-2 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]">
            <AlertTriangle className="h-4 w-4" />
            DEGRADED · 同一法人/期间返回多条正式 VAT 当前记录，未在前端合并，请复核后端 current ledger 约束。
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatutoryKpi label="期初留抵" value={statutoryRecord?.openingInputCredit} />
          <StatutoryKpi label="销项 VAT" value={statutoryRecord?.outputVat} />
          <StatutoryKpi label="进项 VAT" value={statutoryRecord?.inputVat} />
          <StatutoryKpi label="税款预缴" value={statutoryRecord?.taxPrepayment} />
          <StatutoryKpi label="实际应纳 VAT" value={statutoryRecord?.vatPayableAfterPrepayment} />
          <StatutoryKpi label="期末留抵" value={statutoryRecord?.closingInputCredit} />
        </div>
      </section>

      <section aria-label="项目穿透贡献" className="space-y-3 rounded-2xl border border-[#444653]/30 bg-[#171f33]/40 p-4">
        <div>
          <h3 className="text-[15px] font-bold text-[#dae2fd]">项目穿透贡献 (Project Contributions)</h3>
          <p className="mt-1 text-[12px] text-[#8e909f]">以下仅为 LEGAL_ENTITY_PROJECTION 中的项目贡献拆分，不改变项目事实或法人正式 VAT。</p>
        </div>
        <div className="overflow-x-auto rounded-xl border border-[#444653]/30">
          <table className="w-full min-w-[860px] text-[12px] text-[#c4c5d5]">
            <thead className="bg-[#0b1326]/70 text-[#8e909f]">
              <tr>
                <th className="px-3 py-2.5 text-left">项目</th>
                <th className="px-3 py-2.5 text-right">收入贡献</th>
                <th className="px-3 py-2.5 text-right">成本投影</th>
                <th className="px-3 py-2.5 text-right">销项 Projection</th>
                <th className="px-3 py-2.5 text-right">进项 Projection</th>
                <th className="px-3 py-2.5 text-right">内部交易净额</th>
                <th className="px-3 py-2.5 text-right">内部交易 VAT</th>
              </tr>
            </thead>
            <tbody>
              {contributionRows.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-[#8e909f]">当前选择暂无项目贡献 Projection。</td></tr>
              ) : contributionRows.map((item, index) => (
                <ContributionRow key={`${item.projectId ?? 'non-project'}-${index}`} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
