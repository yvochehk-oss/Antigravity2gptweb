import { useState } from 'react';
import {
  Search,
  Download,
  CheckCircle2,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  ReceiptText,
} from 'lucide-react';
import { DataStatus, EntityTaxLedgerRecord, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';
import { EntityVatLineageDrawer } from './EntityVatLineageDrawer';
import { periodStateLabel, runStatusLabel } from './uiLocalization';

type SortField =
  | 'entityName'
  | 'period'
  | 'openingInputCredit'
  | 'outputVat'
  | 'inputVat'
  | 'taxPrepayment'
  | 'vatPayableAfterPrepayment'
  | 'closingInputCredit'
  | null;
type SortOrder = 'asc' | 'desc';

export const TAX_LEDGER_EMPTY_MESSAGE = '接口正常、指定期间暂无已生成台账。请确认底层规范事实已就绪，再由受控确定性重建生成。';

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

const YEAR_OPTIONS = ['2023', '2024', '2025', '2026', '2027', '2028'];
const MONTH_OPTIONS = [
  { value: '01', label: '01月' },
  { value: '02', label: '02月' },
  { value: '03', label: '03月' },
  { value: '04', label: '04月' },
  { value: '05', label: '05月' },
  { value: '06', label: '06月' },
  { value: '07', label: '07月' },
  { value: '08', label: '08月' },
  { value: '09', label: '09月' },
  { value: '10', label: '10月' },
  { value: '11', label: '11月' },
  { value: '12', label: '12月' },
];

function formatAmount(value: number): string {
  return `¥${value.toLocaleString('zh-CN')}`;
}

export function TaxLedgerView({
  records,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onOpenExportModal,
  onRebuildTaxLedger,
  isRebuilding,
}: TaxLedgerViewProps) {
  const now = new Date();
  const defaultYear = String(now.getFullYear());
  const defaultMonth = String(now.getMonth() + 1).padStart(2, '0');

  const [searchWord, setSearchWord] = useState('');
  const [filterYear, setFilterYear] = useState('全部年份');
  const [filterMonth, setFilterMonth] = useState('全部月份');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
  const [rebuildYear, setRebuildYear] = useState(defaultYear);
  const [rebuildMonth, setRebuildMonth] = useState(defaultMonth);
  const [lineageRecord, setLineageRecord] = useState<EntityTaxLedgerRecord | null>(null);
  const rebuildPeriod = `${rebuildYear}-${rebuildMonth}`;

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(previous => (previous === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder(field === 'entityName' ? 'asc' : 'desc');
    }
  };

  const filteredRecords = records.filter(item => {
    const normalizedSearch = searchWord.toLowerCase();
    const matchesSearch =
      item.entityName.toLowerCase().includes(normalizedSearch)
      || item.entityCode.toLowerCase().includes(normalizedSearch)
      || item.businessRole.toLowerCase().includes(normalizedSearch);
    const matchesYear = filterYear === '全部年份' || item.period.startsWith(filterYear);
    const matchesMonth = filterMonth === '全部月份' || item.period.endsWith(`-${filterMonth}`);
    return matchesSearch && matchesYear && matchesMonth;
  });

  const sortedRecords = [...filteredRecords].sort((left, right) => {
    if (!sortField) return 0;
    let comparison = 0;
    if (sortField === 'entityName') comparison = left.entityName.localeCompare(right.entityName, 'zh-CN');
    else if (sortField === 'period') comparison = left.period.localeCompare(right.period);
    else comparison = left[sortField] - right[sortField];
    return sortOrder === 'asc' ? comparison : -comparison;
  });

  const totals = filteredRecords.reduce((sum, record) => ({
    openingInputCredit: sum.openingInputCredit + record.openingInputCredit,
    outputVat: sum.outputVat + record.outputVat,
    inputVat: sum.inputVat + record.inputVat,
    taxPrepayment: sum.taxPrepayment + record.taxPrepayment,
    vatPayableBeforePrepayment: sum.vatPayableBeforePrepayment + record.vatPayableBeforePrepayment,
    vatPayableAfterPrepayment: sum.vatPayableAfterPrepayment + record.vatPayableAfterPrepayment,
    closingInputCredit: sum.closingInputCredit + record.closingInputCredit,
    unappliedTaxPrepayment: sum.unappliedTaxPrepayment + record.unappliedTaxPrepayment,
  }), {
    openingInputCredit: 0,
    outputVat: 0,
    inputVat: 0,
    taxPrepayment: 0,
    vatPayableBeforePrepayment: 0,
    vatPayableAfterPrepayment: 0,
    closingInputCredit: 0,
    unappliedTaxPrepayment: 0,
  });

  const handleRebuild = () => {
    if (!/^(?:\d{4})-(?:0[1-9]|1[0-2])$/.test(rebuildPeriod) || isRebuilding) return;
    const confirmed = window.confirm(
      `将对 ${rebuildPeriod} 执行受控确定性台账生成／重建。该操作会原子替换该期间汇总；请确认底层事实数据已归集就绪。继续吗？`,
    );
    if (confirmed) void onRebuildTaxLedger(rebuildPeriod);
  };

  const pageTitle = (
    <header data-page-title="tax-ledger" className="w-full">
      <div className="flex items-start gap-2.5">
        <ReceiptText className="mt-1 h-7 w-7 flex-shrink-0 text-[#4cd7f6]" />
        <div>
          <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">法人法定税务</h2>
          <p className="mt-1 text-[14px] text-[#c4c5d5]">按独立法人及纳税所属期展示确定性增值税结果。项目经营损益与项目税务分析不进入本申报口径。</p>
        </div>
      </div>
    </header>
  );

  if (dataStatus !== 'READY') {
    return (
      <div className="space-y-6">
        {pageTitle}
        <DataStatusCard status={dataStatus} title="法人法定税务台账不可用" message={dataStatusMessage} onRetry={onRetry} />
      </div>
    );
  }

  const kpis = [
    ['期初留抵', totals.openingInputCredit],
    ['销项税额', totals.outputVat],
    ['进项税额', totals.inputVat],
    ['税款预缴', totals.taxPrepayment],
    ['预缴前应纳税额', totals.vatPayableBeforePrepayment],
    ['实际应纳增值税', totals.vatPayableAfterPrepayment],
    ['期末留抵', totals.closingInputCredit],
    ['未抵完预缴', totals.unappliedTaxPrepayment],
  ] as const;

  const sortableHeader = (label: string, field: Exclude<SortField, null>, align = 'right') => (
    <th
      onClick={() => handleSort(field)}
      className={`py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors ${align === 'right' ? 'text-right' : ''}`}
    >
      <div className={`flex items-center gap-1.5 ${align === 'right' ? 'justify-end' : ''}`}>
        <span>{label}</span>
        {sortField === field
          ? sortOrder === 'asc'
            ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" />
            : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
          : <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />}
      </div>
    </th>
  );

  return (
    <div className="space-y-6">
      {pageTitle}

      <div data-page-controls="tax-ledger" className="glass-panel flex flex-wrap items-center justify-end gap-3 rounded-xl border border-[#444653]/30 p-3.5">
        <button
          type="button"
          onClick={onOpenExportModal}
          className="flex cursor-pointer items-center gap-1.5 rounded-xl border-t border-[#4cd7f6]/30 bg-[#1e40af] px-3.5 py-2 text-[13px] font-semibold text-[#dde1ff] transition-all hover:bg-[#1e40af]/80"
        >
          <Download className="w-4 h-4 text-[#4cd7f6]" />
          <span>导出全量台账</span>
        </button>
      </div>

      <div className="rounded-xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 px-4 py-3" role="note">
        <p className="text-[13px] font-semibold text-[#4cd7f6]">法人法定申报口径</p>
        <p className="mt-1 text-[12px] text-[#c4c5d5]">本页只展示法人增值税法定事实与计算运行状态，不展示营业收入、真实成本、预计利润或企业所得税经营指标。</p>
      </div>

      {records.length === 0 && (
        <div className="rounded-xl border border-[#10B981]/30 bg-[#10B981]/5 p-4" role="status" aria-live="polite">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#10B981]" />
            <p className="text-[13px] text-[#c4c5d5] leading-relaxed">{TAX_LEDGER_EMPTY_MESSAGE}</p>
          </div>
        </div>
      )}

      <div className="glass-panel flex flex-wrap items-center justify-between gap-4 rounded-xl border border-[#4cd7f6]/20 p-4">
        <div>
          <p className="font-semibold text-[#dae2fd]">受控确定性台账生成／重建</p>
          <p className="mt-1 text-[12px] leading-relaxed text-[#c4c5d5]">请确认底层事实数据已归集就绪；确认后将原子替换所选期间汇总。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <select
            id="tax-ledger-rebuild-year"
            value={rebuildYear}
            onChange={event => setRebuildYear(event.target.value)}
            disabled={isRebuilding}
            className="rounded-lg border border-[#444653]/40 bg-[#171f33] px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none"
          >
            {YEAR_OPTIONS.map(year => <option key={year} value={year}>{year}年</option>)}
          </select>
          <select
            id="tax-ledger-rebuild-month"
            value={rebuildMonth}
            onChange={event => setRebuildMonth(event.target.value)}
            disabled={isRebuilding}
            className="rounded-lg border border-[#444653]/40 bg-[#171f33] px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none"
          >
            {MONTH_OPTIONS.map(month => <option key={month.value} value={month.value}>{month.label}</option>)}
          </select>
          <button
            type="button"
            onClick={handleRebuild}
            disabled={isRebuilding}
            className="rounded-lg bg-[#03b5d3] px-3.5 py-1.5 text-[13px] font-semibold text-[#0b1326] transition-colors hover:bg-[#03b5d3]/80 disabled:opacity-50"
          >
            {isRebuilding ? '生成中…' : `生成 ${rebuildPeriod} 台账`}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
        {kpis.map(([label, value]) => (
          <div key={label} className="glass-panel rounded-xl border border-[#444653]/20 p-3.5">
            <div className="text-[11px] text-[#8e909f]">{label}</div>
            <div className="mt-1 text-[17px] font-bold font-mono-num text-[#dae2fd]">{formatAmount(value)}</div>
          </div>
        ))}
      </div>

      <div className="glass-panel flex flex-wrap items-center justify-between gap-3 rounded-xl p-3">
        <div className="relative min-w-[200px] flex-1">
          <Search className="absolute left-3 top-1/2 w-4 h-4 -translate-y-1/2 text-[#8e909f]" />
          <input
            type="text"
            placeholder="搜索法人主体名称、编码、业务角色..."
            value={searchWord}
            onChange={event => setSearchWord(event.target.value)}
            className="w-full rounded-lg border border-[#444653]/30 bg-[#171f33]/60 py-1.5 pl-9 pr-3 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2">
          <select value={filterYear} onChange={event => setFilterYear(event.target.value)} className="rounded-lg border border-[#444653]/40 bg-[#171f33] px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none">
            <option value="全部年份">全部年份</option>
            {YEAR_OPTIONS.map(year => <option key={year} value={year}>{year}年</option>)}
          </select>
          <select value={filterMonth} onChange={event => setFilterMonth(event.target.value)} className="rounded-lg border border-[#444653]/40 bg-[#171f33] px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none">
            <option value="全部月份">全部月份</option>
            {MONTH_OPTIONS.map(month => <option key={month.value} value={month.value}>{month.label}</option>)}
          </select>
        </div>
      </div>

      <div className="glass-panel overflow-hidden rounded-xl border border-[#444653]/30">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="border-b border-[#444653]/40 bg-[#171f33]/80 text-[#8e909f]">
              <tr>
                {sortableHeader('法人', 'entityName', 'left')}
                {sortableHeader('所属期', 'period', 'left')}
                {sortableHeader('期初留抵', 'openingInputCredit')}
                {sortableHeader('销项税额', 'outputVat')}
                {sortableHeader('进项税额', 'inputVat')}
                {sortableHeader('税款预缴', 'taxPrepayment')}
                {sortableHeader('应纳增值税', 'vatPayableAfterPrepayment')}
                {sortableHeader('期末留抵', 'closingInputCredit')}
                <th className="py-2.5 px-3 text-center">期间状态</th>
                <th className="py-2.5 px-3 text-center">计算运行状态</th>
                <th className="py-2.5 px-3 text-center">溯源</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {sortedRecords.length === 0 ? (
                <tr><td colSpan={11} className="py-8 text-center text-[#8e909f]">没有匹配的法人台账记录。</td></tr>
              ) : sortedRecords.map(record => (
                <tr key={record.id} className="transition-colors hover:bg-[#222a3d]/50">
                  <td className="py-2.5 px-3"><div className="font-semibold text-[#dae2fd]">{record.entityName}</div><div className="text-[11px] font-mono-num text-[#8e909f]">{record.entityCode} {record.businessRole ? `· ${record.businessRole}` : ''}</div></td>
                  <td className="whitespace-nowrap py-2.5 px-3 font-mono-num text-[#c4c5d5]">{record.period}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.openingInputCredit)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.outputVat)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.inputVat)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.taxPrepayment)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num font-semibold text-[#10B981]">{formatAmount(record.vatPayableAfterPrepayment)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.closingInputCredit)}</td>
                  <td className="py-2.5 px-3 text-center"><span className="inline-flex rounded-full border border-[#4cd7f6]/30 px-2 py-0.5 text-[11px] text-[#4cd7f6]">{periodStateLabel(record.periodState)}</span></td>
                  <td className="py-2.5 px-3 text-center"><span className="inline-flex rounded-full border border-[#10B981]/30 px-2 py-0.5 text-[11px] text-[#10B981]">{runStatusLabel(record.runStatus)}</span></td>
                  <td className="py-2.5 px-3 text-center"><button type="button" onClick={() => setLineageRecord(record)} className="whitespace-nowrap text-[12px] font-medium text-[#4cd7f6] hover:underline">血缘溯源</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <EntityVatLineageDrawer open={lineageRecord !== null} record={lineageRecord} onClose={() => setLineageRecord(null)} />
    </div>
  );
}
