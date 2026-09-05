import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Download, ReceiptText, RefreshCw, ShieldCheck } from 'lucide-react';
import { fetchJson, postJson } from '../api';
import {
  fetchLegalEntities,
  fetchLegalEntityFactPeriods,
  fetchLegalEntityStatutoryVat,
  fetchLegalEntityVatReadiness,
  LegalEntityMasterData,
  LegalEntityVatReadiness,
} from '../legalEntityApi';
import { DataStatus, EntityTaxLedgerRecord, SystemSettings } from '../types';

export const TAX_LEDGER_EMPTY_MESSAGE = '尚未形成正式法定 VAT 台账。RAG 已确认税务事实与门禁状态见下方；未生成正式资源时不会以 ¥0.00 冒充正式税额。';

type LedgerViewMode = 'current' | 'cumulative';

interface TaxLedgerViewProps {
  records: EntityTaxLedgerRecord[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onRetry?: () => void;
  onOpenNewRecordModal?: () => void;
  onOpenExportModal: () => void;
  onAskAiAboutRisk: (entityName: string) => void;
  onRebuildTaxLedger: (period: string) => Promise<void>;
  isRebuilding: boolean;
  settings?: SystemSettings;
}

interface ExportPayload {
  status: string;
  summary: unknown[];
  details: Array<Record<string, unknown>>;
}

function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function formatAmount(value: number): string {
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function csvCell(value: unknown): string {
  const text = String(value ?? '');
  return `"${text.replaceAll('"', '""')}"`;
}

function triggerCsvDownload(filename: string, rows: string[][]): void {
  const csv = `\ufeff${rows.map(row => row.map(csvCell).join(',')).join('\r\n')}`;
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function TaxLedgerView({ records, dataStatusMessage }: TaxLedgerViewProps) {
  const [entities, setEntities] = useState<LegalEntityMasterData[]>([]);
  const [scope, setScope] = useState('ALL');
  const [viewMode, setViewMode] = useState<LedgerViewMode>('current');
  const [period, setPeriod] = useState(records[0]?.period || currentPeriod());
  const [workspaceRecords, setWorkspaceRecords] = useState<EntityTaxLedgerRecord[]>(records);
  const [readinessItems, setReadinessItems] = useState<LegalEntityVatReadiness[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(dataStatusMessage);
  const [exporting, setExporting] = useState(false);
  const requestSeq = useRef(0);

  const ensureEntities = useCallback(async (): Promise<LegalEntityMasterData[]> => {
    if (entities.length > 0) return entities;
    const master = await fetchLegalEntities();
    setEntities(master.items);
    return master.items;
  }, [entities]);

  const selectedEntities = useCallback((master: LegalEntityMasterData[]) => (
    scope === 'ALL' ? master : master.filter(item => item.canonicalCode === scope)
  ), [scope]);

  const fetchReadinessSet = useCallback(async (
    pairs: Array<{ entity: LegalEntityMasterData; period: string }>,
  ): Promise<{ items: LegalEntityVatReadiness[]; failed: number }> => {
    const settled = await Promise.allSettled(
      pairs.map(item => fetchLegalEntityVatReadiness(item.entity.canonicalCode, item.period)),
    );
    return {
      items: settled.flatMap(item => item.status === 'fulfilled' ? [item.value] : []),
      failed: settled.filter(item => item.status === 'rejected').length,
    };
  }, []);

  const readFormalForReadiness = useCallback(async (
    master: LegalEntityMasterData[],
    readiness: LegalEntityVatReadiness[],
  ): Promise<EntityTaxLedgerRecord[]> => {
    const names = new Map(master.map(item => [item.canonicalCode.toUpperCase(), item.legalName]));
    const formal = readiness.filter(item => item.formalResourceExists);
    const settled = await Promise.allSettled(
      formal.map(item => fetchLegalEntityStatutoryVat(
        undefined,
        item.entityCode,
        item.period,
        names.get(item.entityCode.toUpperCase()) || item.entityCode,
      )),
    );
    return settled.flatMap(item => item.status === 'fulfilled' ? item.value.items : []);
  }, []);

  const loadCurrent = useCallback(async (autoEnsure: boolean, forceRebuild: boolean) => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const master = await ensureEntities();
      const targets = selectedEntities(master);
      const pairs = targets.map(entity => ({ entity, period }));
      let diagnostic = await fetchReadinessSet(pairs);

      const rebuildableCodes = new Set(
        diagnostic.items
          .filter(item => item.rebuildEligible || (forceRebuild && item.formalResourceExists))
          .map(item => item.entityCode.toUpperCase()),
      );
      const rebuildTargets = targets.filter(item => rebuildableCodes.has(item.canonicalCode.toUpperCase()));
      let rebuildFailed = 0;
      if ((autoEnsure || forceRebuild) && rebuildTargets.length > 0) {
        const rebuilt = await Promise.allSettled(
          rebuildTargets.map(item => postJson<unknown>(
            `/api/v3/legal-entities/${encodeURIComponent(item.canonicalCode)}/statutory-vat/rebuild?period=${encodeURIComponent(period)}`,
            {},
          )),
        );
        rebuildFailed = rebuilt.filter(item => item.status === 'rejected').length;
        diagnostic = await fetchReadinessSet(pairs);
      }

      const loaded = await readFormalForReadiness(master, diagnostic.items);
      if (seq !== requestSeq.current) return;
      setReadinessItems(diagnostic.items);
      setWorkspaceRecords(loaded);
      const formal = diagnostic.items.filter(item => item.formalResourceExists).length;
      const blocked = diagnostic.items.filter(item => !item.formalResourceExists && !item.rebuildEligible).length;
      const ready = diagnostic.items.filter(item => item.rebuildEligible).length;
      setMessage(
        `${period}：正式台账 ${formal}/${targets.length}；可生成 ${ready}；证据审核/法定门禁阻断 ${blocked}`
        + (diagnostic.failed ? `；诊断失败 ${diagnostic.failed}` : '')
        + (rebuildFailed ? `；重算失败 ${rebuildFailed}` : '')
        + '。',
      );
    } catch (error) {
      if (seq !== requestSeq.current) return;
      setWorkspaceRecords([]);
      setReadinessItems([]);
      setMessage(error instanceof Error ? error.message : '法人法定 VAT 当期加载失败。');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [ensureEntities, fetchReadinessSet, period, readFormalForReadiness, selectedEntities]);

  const loadCumulative = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const master = await ensureEntities();
      const targets = selectedEntities(master);
      const periodSets = await Promise.allSettled(targets.map(async entity => ({
        entity,
        periods: (await fetchLegalEntityFactPeriods(entity.canonicalCode)).periods.map(item => item.period),
      })));
      const pairs = periodSets.flatMap(result => result.status === 'fulfilled'
        ? result.value.periods.map(itemPeriod => ({ entity: result.value.entity, period: itemPeriod }))
        : []);
      const diagnostic = await fetchReadinessSet(pairs);
      const loaded = await readFormalForReadiness(master, diagnostic.items);
      if (seq !== requestSeq.current) return;
      setReadinessItems(diagnostic.items);
      setWorkspaceRecords(loaded);
      const factPeriods = diagnostic.items.filter(item => item.facts.hasBusinessFacts).length;
      const formal = diagnostic.items.filter(item => item.formalResourceExists).length;
      const blocked = diagnostic.items.filter(item => !item.formalResourceExists && !item.rebuildEligible).length;
      const ready = diagnostic.items.filter(item => item.rebuildEligible).length;
      setMessage(
        `累计诊断：发现业务期间 ${factPeriods}，正式期间 ${formal}，可生成 ${ready}，证据审核阻断 ${blocked}`
        + (diagnostic.failed ? `，诊断失败 ${diagnostic.failed}` : '')
        + '。',
      );
    } catch (error) {
      if (seq !== requestSeq.current) return;
      setWorkspaceRecords([]);
      setReadinessItems([]);
      setMessage(error instanceof Error ? error.message : '累计法定 VAT 加载失败。');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [ensureEntities, fetchReadinessSet, readFormalForReadiness, selectedEntities]);

  useEffect(() => {
    void ensureEntities().catch(error => setMessage(error instanceof Error ? error.message : '法人主数据加载失败。'));
  }, [ensureEntities]);

  useEffect(() => {
    if (viewMode === 'current') void loadCurrent(true, false);
    else void loadCumulative();
  }, [loadCurrent, loadCumulative, scope, viewMode, period]);

  const totals = useMemo(() => workspaceRecords.reduce((sum, row) => ({
    openingInputCredit: sum.openingInputCredit + row.openingInputCredit,
    outputVat: sum.outputVat + row.outputVat,
    inputVat: sum.inputVat + row.inputVat,
    taxPrepayment: sum.taxPrepayment + row.taxPrepayment,
    vatPayableAfterPrepayment: sum.vatPayableAfterPrepayment + row.vatPayableAfterPrepayment,
    closingInputCredit: sum.closingInputCredit + row.closingInputCredit,
  }), { openingInputCredit: 0, outputVat: 0, inputVat: 0, taxPrepayment: 0, vatPayableAfterPrepayment: 0, closingInputCredit: 0 }), [workspaceRecords]);

  const previewTotals = useMemo(() => readinessItems.reduce((sum, item) => ({
    outputVat: sum.outputVat + item.facts.observedOutputVat,
    inputVat: sum.inputVat + item.facts.observedInputVat,
    prepayment: sum.prepayment + item.facts.observedPrepayment,
  }), { outputVat: 0, inputVat: 0, prepayment: 0 }), [readinessItems]);

  const coverage = useMemo(() => ({
    discovered: readinessItems.filter(item => item.facts.hasBusinessFacts).length,
    formal: readinessItems.filter(item => item.formalResourceExists).length,
    ready: readinessItems.filter(item => item.rebuildEligible).length,
    blocked: readinessItems.filter(item => !item.formalResourceExists && !item.rebuildEligible).length,
  }), [readinessItems]);

  const blockedItems = useMemo(() => readinessItems.filter(
    item => !item.formalResourceExists && !item.rebuildEligible,
  ), [readinessItems]);

  const exportLedger = async () => {
    setExporting(true);
    try {
      const query = new URLSearchParams({ view: viewMode });
      if (viewMode === 'current') query.set('period', period);
      const payload = await fetchJson<ExportPayload>(
        `/api/v3/legal-entities/${encodeURIComponent(scope)}/statutory-vat/export?${query.toString()}`,
      );
      const rows: string[][] = [
        ['法人法定税务台账导出'],
        ['范围', scope === 'ALL' ? '全系统所有单位' : scope, '视图', viewMode === 'current' ? '当期' : '累计', '期间', viewMode === 'current' ? period : '全部正式期间'],
        [], ['汇总数据'],
        ['法人编码', '期间', '期初留抵', '销项税额', '进项税额', '税款预缴', '实际应纳增值税', '期末留抵'],
      ];
      for (const item of payload.summary) {
        const data = item as Record<string, unknown>;
        const vat = (data.vat_ledger ?? {}) as Record<string, unknown>;
        rows.push([String(data.entity_code ?? ''), String(data.period ?? ''), String(vat.opening_input_credit ?? ''), String(vat.output_vat ?? ''), String(vat.input_vat ?? ''), String(vat.tax_prepayment ?? ''), String(vat.vat_payable_after_prepayment ?? ''), String(vat.closing_input_credit ?? '')]);
      }
      rows.push([], ['交易明细'], ['时间', '法人编码', '来源', '对应合同', '对应发票/缴款书', '交易对手', '具体税种', '税额', '含税金额']);
      for (const item of payload.details) {
        rows.push([String(item.transaction_date ?? ''), String(item.entity_code ?? ''), String(item.source_type ?? ''), String(item.contract_no ?? ''), String(item.invoice_no ?? item.receipt_no ?? ''), String(item.counterparty ?? ''), String(item.tax_type ?? ''), String(item.tax_amount ?? ''), String(item.gross_amount ?? '')]);
      }
      triggerCsvDownload(`法人法定税务_${scope}_${viewMode}_${viewMode === 'current' ? period : '累计'}.csv`, rows);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '台账导出失败。');
    } finally {
      setExporting(false);
    }
  };

  const hasFormal = workspaceRecords.length > 0;
  const coverageText = readinessItems.length > 0 ? `正式覆盖 ${coverage.formal}/${readinessItems.length}` : '暂无正式覆盖';
  const kpis = [
    ['期初留抵', totals.openingInputCredit], ['销项税额', totals.outputVat], ['进项税额', totals.inputVat],
    ['税款预缴', totals.taxPrepayment], ['实际应纳增值税', totals.vatPayableAfterPrepayment], ['期末留抵', totals.closingInputCredit],
  ] as const;

  return (
    <div className="space-y-6">
      <header data-page-title="tax-ledger" className="w-full">
        <div className="flex items-start gap-2.5"><ReceiptText className="mt-1 h-7 w-7 flex-shrink-0 text-brand" /><div><h2 className="text-[28px] font-bold tracking-tight text-primary">法人法定税务</h2><p className="mt-1 text-[14px] text-secondary">正式法定口径与 RAG PostgreSQL 已确认税务事实严格分层；门禁只控制正式台账生成，不否定已经发生的税务事实。</p></div></div>
      </header>

      <div className="surface-card flex flex-wrap items-center gap-3 rounded-xl p-4">
        <select value={scope} onChange={event => setScope(event.target.value)} className="rounded-lg border border-default bg-surface px-3 py-2 text-[13px] text-primary"><option value="ALL">全系统所有单位归总（ALL）</option>{entities.map(entity => <option key={entity.canonicalCode} value={entity.canonicalCode}>{entity.legalName}（{entity.canonicalCode}）</option>)}</select>
        <div className="flex rounded-lg border border-default p-1"><button type="button" onClick={() => setViewMode('current')} className={`rounded-md px-3 py-1.5 text-[13px] font-semibold ${viewMode === 'current' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary'}`}>当期</button><button type="button" onClick={() => setViewMode('cumulative')} className={`rounded-md px-3 py-1.5 text-[13px] font-semibold ${viewMode === 'cumulative' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary'}`}>汇总／累计</button></div>
        {viewMode === 'current' && <input type="month" value={period} onChange={event => setPeriod(event.target.value)} className="rounded-lg border border-default bg-surface px-3 py-2 text-[13px] text-primary" />}
        <button type="button" disabled={loading || viewMode !== 'current'} onClick={() => void loadCurrent(false, true)} className="flex items-center gap-1.5 rounded-lg bg-[var(--color-brand)] px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />重新计算当期台账</button>
        <button type="button" disabled={exporting || loading} onClick={() => void exportLedger()} className="ml-auto flex items-center gap-1.5 rounded-lg border border-default bg-surface px-4 py-2 text-[13px] font-semibold text-primary disabled:opacity-50"><Download className="h-4 w-4" />{exporting ? '导出中…' : '导出台账总额 + 交易明细'}</button>
      </div>

      <div className="surface-card rounded-xl px-4 py-3 text-[12px] text-secondary" role="status">{loading ? '正在读取正式 VAT 与 readiness 门禁状态…' : message || TAX_LEDGER_EMPTY_MESSAGE}</div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">{kpis.map(([label, value]) => <div key={label} className="surface-card rounded-xl p-3.5"><div className="text-[11px] text-secondary">{label}</div><div className="mt-1 text-[17px] font-bold text-primary">{hasFormal ? formatAmount(value) : '——'}</div><div className="mt-1 text-[10px] text-secondary">{hasFormal ? coverageText : '尚未形成正式台账'}</div></div>)}</div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="surface-card rounded-xl p-4">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-primary"><ShieldCheck className="h-4 w-4 text-brand" />正式法定覆盖与门禁</div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-[12px] sm:grid-cols-4">
            <div><div className="text-secondary">{viewMode === 'cumulative' ? '发现业务期间' : '发现业务范围'}</div><div className="mt-1 text-lg font-bold text-primary">{coverage.discovered}</div></div>
            <div><div className="text-secondary">正式期间</div><div className="mt-1 text-lg font-bold text-primary">{coverage.formal}</div></div>
            <div><div className="text-secondary">可生成</div><div className="mt-1 text-lg font-bold text-primary">{coverage.ready}</div></div>
            <div><div className="text-secondary">证据审核阻断</div><div className="mt-1 text-lg font-bold text-primary">{coverage.blocked}</div></div>
          </div>
        </div>
        <div className="surface-card rounded-xl p-4">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-primary"><AlertTriangle className="h-4 w-4" />RAG 已确认税务事实（FACT）</div>
          <div className="mt-3 grid grid-cols-3 gap-3 text-[12px]"><div><div className="text-secondary">已确认销项税务事实</div><div className="mt-1 font-bold text-primary">{formatAmount(previewTotals.outputVat)}</div></div><div><div className="text-secondary">已确认进项税务事实</div><div className="mt-1 font-bold text-primary">{formatAmount(previewTotals.inputVat)}</div></div><div><div className="text-secondary">实际预缴税款事实</div><div className="mt-1 font-bold text-primary">{formatAmount(previewTotals.prepayment)}</div></div></div>
          <div className="mt-3 text-[11px] text-secondary">以上来自 RAG PostgreSQL 的已确认／VALID 当前事实。Formal Gate 不会否定这些已经发生的数据；它们可进入 Tax 建议计算，但不等同于本期最终法定应纳税额。</div>
        </div>
      </div>

      {blockedItems.length > 0 && <div className="surface-card rounded-xl p-4"><div className="text-[13px] font-semibold text-primary">门禁阻断明细</div><div className="mt-3 space-y-2">{blockedItems.slice(0, 20).map(item => <div key={`${item.entityCode}-${item.period}`} className="rounded-lg border border-default px-3 py-2 text-[12px]"><div className="font-semibold text-primary">{item.entityCode} · {item.period} · {item.formalStatus}</div><div className="mt-1 text-secondary">{item.blockingReasons.map(reason => `${reason.code}：${reason.message}`).join('；') || '尚未满足正式法定台账生成条件。'}</div></div>)}</div>{blockedItems.length > 20 && <div className="mt-2 text-[11px] text-secondary">另有 {blockedItems.length - 20} 条阻断记录未展开。</div>}</div>}

      <div className="surface-card overflow-hidden rounded-xl"><div className="overflow-x-auto"><table className="w-full text-left text-[13px]"><thead className="border-b border-default bg-surface-2 text-secondary"><tr><th className="px-3 py-2.5">法人主体</th><th className="px-3 py-2.5">期间</th><th className="px-3 py-2.5 text-right">销项税额</th><th className="px-3 py-2.5 text-right">进项税额</th><th className="px-3 py-2.5 text-right">税款预缴</th><th className="px-3 py-2.5 text-right">实际应纳增值税</th><th className="px-3 py-2.5 text-right">期末留抵</th></tr></thead><tbody className="divide-y divide-default">{workspaceRecords.map(row => <tr key={`${row.entityCode}-${row.period}-${row.id}`}><td className="px-3 py-3"><div className="font-semibold text-primary">{row.entityName}</div><div className="text-[11px] text-secondary">{row.entityCode}</div></td><td className="px-3 py-3 text-secondary">{row.period}</td><td className="px-3 py-3 text-right">{formatAmount(row.outputVat)}</td><td className="px-3 py-3 text-right">{formatAmount(row.inputVat)}</td><td className="px-3 py-3 text-right">{formatAmount(row.taxPrepayment)}</td><td className="px-3 py-3 text-right font-semibold text-primary">{formatAmount(row.vatPayableAfterPrepayment)}</td><td className="px-3 py-3 text-right">{formatAmount(row.closingInputCredit)}</td></tr>)}{!loading && workspaceRecords.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-secondary">——（尚未形成正式台账）<div className="mt-2 text-[11px]">请查看上方 RAG 已确认税务事实与门禁阻断明细。</div></td></tr>}</tbody></table></div></div>
    </div>
  );
}
