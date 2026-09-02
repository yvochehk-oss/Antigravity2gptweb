import { useState } from 'react';
import {
  Search,
  Download,
  Database,
  CheckCircle2,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react';
import { DataStatus, EntityTaxLedgerRecord, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';
import { EntityVatLineageDrawer } from './EntityVatLineageDrawer';

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

export const TAX_LEDGER_EMPTY_MESSAGE = '接口正常、指定期间暂无已生成台账。请先完成 RAG 凭证同步/结构化入库，再由受控确定性重建生成。';

interface TaxLedgerViewProps {
  records: EntityTaxLedgerRecord[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onRetry?: () => void;
  onOpenNewRecordModal: () => void;
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
  onOpenNewRecordModal,
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
      `将对 ${rebuildPeriod} 执行受控确定性台账生成/重建。该操作会原子替换该期间汇总；请确认已完成 RAG 凭证同步/结构化入库。继续吗？`,
    );
    if (confirmed) void onRebuildTaxLedger(rebuildPeriod);
  };

  if (dataStatus !== 'READY') {
    return (
      <div className="space-y-6">
        <h2 className="text-[28px] font-bold text-[#dae2fd]">法人月度确定性税务台账</h2>
        <DataStatusCard status={dataStatus} title="法人税务台账不可用" message={dataStatusMessage} onRetry={onRetry} />
      </div>
    );
  }

  const kpis = [
    ['期初留抵', totals.openingInputCredit],
    ['销项 VAT', totals.outputVat],
    ['进项 VAT', totals.inputVat],
    ['税款预缴', totals.taxPrepayment],
    ['预缴前应纳', totals.vatPayableBeforePrepayment],
    ['实际应纳 VAT', totals.vatPayableAfterPrepayment],
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
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight">法人月度确定性税务台账</h2>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            按独立法人及纳税所属期展示确定性 VAT 结果。项目经营损益与项目税务分析不进入本申报口径。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onOpenNewRecordModal}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#03b5d3]/20 hover:bg-[#03b5d3]/30 text-[#4cd7f6] text-[13px] font-semibold border border-[#4cd7f6]/40 transition-all cursor-pointer"
          >
            <Database className="w-4 h-4" />
            <span>RAG 凭证同步与智能查账</span>
          </button>
          <button
            type="button"
            onClick={onOpenExportModal}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] text-[13px] font-semibold border-t border-[#4cd7f6]/30 transition-all cursor-pointer"
          >
            <Download className="w-4 h-4 text-[#4cd7f6]" />
            <span>导出全量台账</span>
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 px-4 py-3" role="note">
        <p className="text-[13px] font-semibold text-[#4cd7f6]">LEGAL_ENTITY_STATUTORY 法人法定申报口径</p>
        <p className="text-[12px] text-[#c4c5d5] mt-1">本页只展示法人 VAT 法定事实与计算运行状态，不展示营业收入、真实成本、预计利润或所得税经营指标。</p>
      </div>

      {records.length === 0 && (
        <div className="rounded-xl border border-[#10B981]/30 bg-[#10B981]/5 p-4" role="status" aria-live="polite">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#10B981]" />
            <p className="text-[13px] text-[#c4c5d5] leading-relaxed">{TAX_LEDGER_EMPTY_MESSAGE}</p>
          </div>
        </div>
      )}

      <div className="glass-panel rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 border border-[#4cd7f6]/20">
        <div>
          <p className="font-semibold text-[#dae2fd]">受控确定性台账生成/重建</p>
          <p className="text-[12px] text-[#c4c5d5] mt-1 leading-relaxed">请先完成 RAG 凭证同步/结构化入库；确认后将原子替换所选期间汇总。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <select
            id="tax-ledger-rebuild-year"
            value={rebuildYear}
            onChange={event => setRebuildYear(event.target.value)}
            disabled={isRebuilding}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            {YEAR_OPTIONS.map(year => <option key={year} value={year}>{year}年</option>)}
          </select>
          <select
            id="tax-ledger-rebuild-month"
            value={rebuildMonth}
            onChange={event => setRebuildMonth(event.target.value)}
            disabled={isRebuilding}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            {MONTH_OPTIONS.map(month => <option key={month.value} value={month.value}>{month.label}</option>)}
          </select>
          <button
            type="button"
            onClick={handleRebuild}
            disabled={isRebuilding}
            className="px-3.5 py-1.5 rounded-lg bg-[#03b5d3] hover:bg-[#03b5d3]/80 disabled:opacity-50 text-[#0b1326] font-semibold text-[13px] transition-colors"
          >
            {isRebuilding ? '生成中...' : `生成 ${rebuildPeriod} 台账`}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-3">
        {kpis.map(([label, value]) => (
          <div key={label} className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
            <div className="text-[11px] text-[#8e909f]">{label}</div>
            <div className="text-[17px] font-bold text-[#dae2fd] mt-1 font-mono-num">{formatAmount(value)}</div>
          </div>
        ))}
      </div>

      <div className="glass-panel rounded-xl p-3 flex flex-wrap items-center justify-between gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f]" />
          <input
            type="text"
            placeholder="搜索法人主体名称、编码、业务角色..."
            value={searchWord}
            onChange={event => setSearchWord(event.target.value)}
            className="w-full bg-[#171f33]/60 border border-[#444653]/30 rounded-lg pl-9 pr-3 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          />
        </div>
        <div className="flex items-center gap-2">
          <select
            value={filterYear}
            onChange={event => setFilterYear(event.target.value)}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            <option value="全部年份">全部年份</option>
            {YEAR_OPTIONS.map(year => <option key={year} value={year}>{year}年</option>)}
          </select>
          <select
            value={filterMonth}
            onChange={event => setFilterMonth(event.target.value)}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            <option value="全部月份">全部月份</option>
            {MONTH_OPTIONS.map(month => <option key={month.value} value={month.value}>{month.label}</option>)}
          </select>
        </div>
      </div>

      <div className="glass-panel rounded-xl overflow-hidden border border-[#444653]/30">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-[#171f33]/80 border-b border-[#444653]/40 text-[#8e909f]">
              <tr>
                {sortableHeader('法人', 'entityName', 'left')}
                {sortableHeader('所属期', 'period', 'left')}
                {sortableHeader('期初留抵', 'openingInputCredit')}
                {sortableHeader('销项', 'outputVat')}
                {sortableHeader('进项', 'inputVat')}
                {sortableHeader('预缴', 'taxPrepayment')}
                {sortableHeader('应纳', 'vatPayableAfterPrepayment')}
                {sortableHeader('期末留抵', 'closingInputCredit')}
                <th className="py-2.5 px-3 text-center">期间状态</th>
                <th className="py-2.5 px-3 text-center">Run 状态</th>
                <th className="py-2.5 px-3 text-center">溯源</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {sortedRecords.length === 0 ? (
                <tr>
                  <td colSpan={11} className="py-8 text-center text-[#8e909f]">没有匹配的法人台账记录。</td>
                </tr>
              ) : sortedRecords.map(record => (
                <tr key={record.id} className="hover:bg-[#222a3d]/50 transition-colors">
                  <td className="py-2.5 px-3">
                    <div className="font-semibold text-[#dae2fd]">{record.entityName}</div>
                    <div className="text-[11px] text-[#8e909f] font-mono-num">{record.entityCode} {record.businessRole ? `· ${record.businessRole}` : ''}</div>
                  </td>
                  <td className="py-2.5 px-3 font-mono-num text-[#c4c5d5] whitespace-nowrap">{record.period}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.openingInputCredit)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.outputVat)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.inputVat)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.taxPrepayment)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num font-semibold text-[#10B981]">{formatAmount(record.vatPayableAfterPrepayment)}</td>
                  <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">{formatAmount(record.closingInputCredit)}</td>
                  <td className="py-2.5 px-3 text-center"><span className="inline-flex px-2 py-0.5 rounded-full text-[11px] border border-[#4cd7f6]/30 text-[#4cd7f6]">{record.periodState}</span></td>
                  <td className="py-2.5 px-3 text-center"><span className="inline-flex px-2 py-0.5 rounded-full text-[11px] border border-[#10B981]/30 text-[#10B981]">{record.runStatus}</span></td>
                  <td className="py-2.5 px-3 text-center">
                    <button
                      type="button"
                      onClick={() => setLineageRecord(record)}
                      className="whitespace-nowrap text-[12px] font-medium text-[#4cd7f6] hover:underline"
                    >
                      血缘溯源
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <EntityVatLineageDrawer
        open={lineageRecord !== null}
        record={lineageRecord}
        onClose={() => setLineageRecord(null)}
      />
    </div>
  );
}
