import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, ReceiptText, RefreshCw } from 'lucide-react';
import { fetchJson, postJson } from '../api';
import {
  fetchLegalEntities,
  fetchLegalEntityFactPeriods,
  fetchLegalEntityStatutoryVat,
  LegalEntityMasterData,
} from '../legalEntityApi';
import { DataStatus, EntityTaxLedgerRecord, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';

export const TAX_LEDGER_EMPTY_MESSAGE = '接口正常、指定期间暂无已生成台账。系统会在进入当期或切换单位时自动尝试生成；仍为空时请检查法定税务证据门禁。';

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

export function TaxLedgerView({ records, dataStatus, dataStatusMessage, onRetry }: TaxLedgerViewProps) {
  const [entities, setEntities] = useState<LegalEntityMasterData[]>([]);
  const [scope, setScope] = useState('ALL');
  const [viewMode, setViewMode] = useState<LedgerViewMode>('current');
  const [period, setPeriod] = useState(records[0]?.period || currentPeriod());
  const [workspaceRecords, setWorkspaceRecords] = useState<EntityTaxLedgerRecord[]>(records);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(dataStatusMessage);
  const [exporting, setExporting] = useState(false);
  const requestSeq = useRef(0);

  useEffect(() => {
    if (records.length > 0 && workspaceRecords.length === 0) setWorkspaceRecords(records);
  }, [records, workspaceRecords.length]);

  const ensureEntities = useCallback(async (): Promise<LegalEntityMasterData[]> => {
    if (entities.length > 0) return entities;
    const master = await fetchLegalEntities();
    setEntities(master.items);
    return master.items;
  }, [entities]);

  const selectedEntities = useCallback((master: LegalEntityMasterData[]) => (
    scope === 'ALL' ? master : master.filter(item => item.canonicalCode === scope)
  ), [scope]);

  const readCurrent = useCallback(async (master: LegalEntityMasterData[]) => {
    const targets = selectedEntities(master);
    const settled = await Promise.allSettled(
      targets.map(item => fetchLegalEntityStatutoryVat(undefined, item.canonicalCode, period, item.legalName)),
    );
    const loaded = settled.flatMap(result => result.status === 'fulfilled' ? result.value.items : []);
    const missing = targets.filter(target => !loaded.some(item => item.entityCode === target.canonicalCode));
    return { loaded, missing };
  }, [period, selectedEntities]);

  const rebuildTargets = useCallback(async (targets: LegalEntityMasterData[]) => Promise.allSettled(
    targets.map(item => postJson<unknown>(
      `/api/v3/legal-entities/${encodeURIComponent(item.canonicalCode)}/statutory-vat/rebuild?period=${encodeURIComponent(period)}`,
      {},
    )),
  ), [period]);

  const loadCurrent = useCallback(async (autoEnsure: boolean, forceRebuild: boolean) => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const master = await ensureEntities();
      const targets = selectedEntities(master);
      let result = await readCurrent(master);
      const rebuildList = forceRebuild ? targets : autoEnsure ? result.missing : [];
      let blocked = 0;
      if (rebuildList.length > 0) {
        const rebuilt = await rebuildTargets(rebuildList);
        blocked = rebuilt.filter(item => item.status === 'rejected').length;
        result = await readCurrent(master);
      }
      if (seq !== requestSeq.current) return;
      setWorkspaceRecords(result.loaded);
      setMessage(blocked > 0
        ? `${period} 已自动计算可生成单位；另有 ${blocked} 个单位被法定证据门禁阻止。`
        : `${period} 已加载 ${result.loaded.length} 个法人当期法定 VAT 台账。`);
    } catch (error) {
      if (seq !== requestSeq.current) return;
      setWorkspaceRecords([]);
      setMessage(error instanceof Error ? error.message : '法人法定 VAT 当期加载失败。');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [ensureEntities, period, readCurrent, rebuildTargets, selectedEntities]);

  const loadCumulative = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const master = await ensureEntities();
      const targets = selectedEntities(master);
      const groups = await Promise.allSettled(targets.map(async entity => {
        const periods = await fetchLegalEntityFactPeriods(entity.canonicalCode);
        const rows = await Promise.allSettled(
          periods.periods.map(item => fetchLegalEntityStatutoryVat(undefined, entity.canonicalCode, item.period, entity.legalName)),
        );
        return rows.flatMap(item => item.status === 'fulfilled' ? item.value.items : []);
      }));
      if (seq !== requestSeq.current) return;
      const loaded = groups.flatMap(item => item.status === 'fulfilled' ? item.value : []);
      setWorkspaceRecords(loaded);
      setMessage(`累计视图已加载 ${loaded.length} 条法人月度法定 VAT 记录。`);
    } catch (error) {
      if (seq !== requestSeq.current) return;
      setWorkspaceRecords([]);
      setMessage(error instanceof Error ? error.message : '累计法定 VAT 加载失败。');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [ensureEntities, selectedEntities]);

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

  const effectiveStatus: DataStatus = loading ? 'LOADING' : dataStatus === 'UNAVAILABLE' && workspaceRecords.length === 0 ? 'UNAVAILABLE' : 'READY';
  if (effectiveStatus === 'UNAVAILABLE') {
    return <DataStatusCard status="UNAVAILABLE" title="法人法定税务台账不可用" message={message || dataStatusMessage} onRetry={onRetry} />;
  }

  const kpis = [
    ['期初留抵', totals.openingInputCredit], ['销项税额', totals.outputVat], ['进项税额', totals.inputVat],
    ['税款预缴', totals.taxPrepayment], ['实际应纳增值税', totals.vatPayableAfterPrepayment], ['期末留抵', totals.closingInputCredit],
  ] as const;

  return (
    <div className="space-y-6">
      <header data-page-title="tax-ledger" className="w-full">
        <div className="flex items-start gap-2.5"><ReceiptText className="mt-1 h-7 w-7 flex-shrink-0 text-brand" /><div><h2 className="text-[28px] font-bold tracking-tight text-primary">法人法定税务</h2><p className="mt-1 text-[14px] text-secondary">支持全系统所有单位归总与单法人下钻；当期自动确保正式 VAT 台账，累计视图只读取已形成的法定资源。</p></div></div>
      </header>
      <div className="surface-card flex flex-wrap items-center gap-3 rounded-xl p-4">
        <select value={scope} onChange={event => setScope(event.target.value)} className="rounded-lg border border-default bg-surface px-3 py-2 text-[13px] text-primary"><option value="ALL">全系统所有单位归总（ALL）</option>{entities.map(entity => <option key={entity.canonicalCode} value={entity.canonicalCode}>{entity.legalName}（{entity.canonicalCode}）</option>)}</select>
        <div className="flex rounded-lg border border-default p-1"><button type="button" onClick={() => setViewMode('current')} className={`rounded-md px-3 py-1.5 text-[13px] font-semibold ${viewMode === 'current' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary'}`}>当期</button><button type="button" onClick={() => setViewMode('cumulative')} className={`rounded-md px-3 py-1.5 text-[13px] font-semibold ${viewMode === 'cumulative' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary'}`}>汇总／累计</button></div>
        {viewMode === 'current' && <input type="month" value={period} onChange={event => setPeriod(event.target.value)} className="rounded-lg border border-default bg-surface px-3 py-2 text-[13px] text-primary" />}
        <button type="button" disabled={loading || viewMode !== 'current'} onClick={() => void loadCurrent(false, true)} className="flex items-center gap-1.5 rounded-lg bg-[var(--color-brand)] px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />重新计算当期台账</button>
        <button type="button" disabled={exporting || loading} onClick={() => void exportLedger()} className="ml-auto flex items-center gap-1.5 rounded-lg border border-default bg-surface px-4 py-2 text-[13px] font-semibold text-primary disabled:opacity-50"><Download className="h-4 w-4" />{exporting ? '导出中…' : '导出台账总额 + 交易明细'}</button>
      </div>
      <div className="surface-card rounded-xl px-4 py-3 text-[12px] text-secondary" role="status">{loading ? '正在自动计算／读取法人法定 VAT 台账…' : message || TAX_LEDGER_EMPTY_MESSAGE}</div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">{kpis.map(([label, value]) => <div key={label} className="surface-card rounded-xl p-3.5"><div className="text-[11px] text-secondary">{label}</div><div className="mt-1 text-[17px] font-bold text-primary">{formatAmount(value)}</div></div>)}</div>
      <div className="surface-card overflow-hidden rounded-xl"><div className="overflow-x-auto"><table className="w-full text-left text-[13px]"><thead className="border-b border-default bg-surface-2 text-secondary"><tr><th className="px-3 py-2.5">法人主体</th><th className="px-3 py-2.5">期间</th><th className="px-3 py-2.5 text-right">销项税额</th><th className="px-3 py-2.5 text-right">进项税额</th><th className="px-3 py-2.5 text-right">税款预缴</th><th className="px-3 py-2.5 text-right">实际应纳增值税</th><th className="px-3 py-2.5 text-right">期末留抵</th></tr></thead><tbody className="divide-y divide-default">{workspaceRecords.map(row => <tr key={`${row.entityCode}-${row.period}-${row.id}`}><td className="px-3 py-3"><div className="font-semibold text-primary">{row.entityName}</div><div className="text-[11px] text-secondary">{row.entityCode}</div></td><td className="px-3 py-3 text-secondary">{row.period}</td><td className="px-3 py-3 text-right">{formatAmount(row.outputVat)}</td><td className="px-3 py-3 text-right">{formatAmount(row.inputVat)}</td><td className="px-3 py-3 text-right">{formatAmount(row.taxPrepayment)}</td><td className="px-3 py-3 text-right font-semibold text-primary">{formatAmount(row.vatPayableAfterPrepayment)}</td><td className="px-3 py-3 text-right">{formatAmount(row.closingInputCredit)}</td></tr>)}{!loading && workspaceRecords.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-secondary">{TAX_LEDGER_EMPTY_MESSAGE}</td></tr>}</tbody></table></div></div>
    </div>
  );
}
