import { useState } from 'react';
import { 
  Search, 
  Download, 
  Database,
  CheckCircle2, 
  ArrowUpDown,
  ArrowUp,
  ArrowDown
} from 'lucide-react';
import { DataStatus, EntityTaxLedgerRecord, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';

type SortField = 'entityName' | 'period' | 'revenue' | 'outputVat' | 'inputVat' | 'vatPayable' | 'realCost' | 'estimatedProfit' | 'estimatedCit' | null;
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

export function TaxLedgerView({
  records,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onOpenNewRecordModal,
  onOpenExportModal,
  onAskAiAboutRisk,
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
  const rebuildPeriod = `${rebuildYear}-${rebuildMonth}`;

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder(field === 'entityName' ? 'asc' : 'desc');
    }
  };

  const hasNoRecords = records.length === 0;

  const filteredRecords = records.filter(item => {
    const matchesSearch = 
      item.entityName.toLowerCase().includes(searchWord.toLowerCase()) ||
      item.entityCode.toLowerCase().includes(searchWord.toLowerCase()) ||
      item.businessRole.toLowerCase().includes(searchWord.toLowerCase());
    
    const matchesYear = filterYear === '全部年份' || item.period.startsWith(filterYear);
    const matchesMonth = filterMonth === '全部月份' || item.period.endsWith(`-${filterMonth}`);

    return matchesSearch && matchesYear && matchesMonth;
  });

  const sortedRecords = [...filteredRecords].sort((a, b) => {
    if (!sortField) return 0;

    let res = 0;
    if (sortField === 'entityName') {
      res = a.entityName.localeCompare(b.entityName, 'zh-CN');
    } else if (sortField === 'period') {
      res = a.period.localeCompare(b.period);
    } else if (sortField === 'revenue') {
      res = a.revenue - b.revenue;
    } else if (sortField === 'outputVat') {
      res = a.outputVat - b.outputVat;
    } else if (sortField === 'inputVat') {
      res = a.inputVat - b.inputVat;
    } else if (sortField === 'vatPayable') {
      res = a.vatPayable - b.vatPayable;
    } else if (sortField === 'realCost') {
      res = a.realCost - b.realCost;
    } else if (sortField === 'estimatedProfit') {
      res = a.estimatedProfit - b.estimatedProfit;
    } else if (sortField === 'estimatedCit') {
      res = a.estimatedCit - b.estimatedCit;
    }

    return sortOrder === 'asc' ? res : -res;
  });

  const totalRevenue = filteredRecords.reduce((acc, cur) => acc + cur.revenue, 0);
  const totalOutputVat = filteredRecords.reduce((acc, cur) => acc + cur.outputVat, 0);
  const totalInputVat = filteredRecords.reduce((acc, cur) => acc + cur.inputVat, 0);
  const totalVatPayable = filteredRecords.reduce((acc, cur) => acc + cur.vatPayable, 0);
  const totalRealCost = filteredRecords.reduce((acc, cur) => acc + cur.realCost, 0);

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

  return (
    <div className="space-y-6">
      {/* 顶部标题与汇总 */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight">法人月度确定性税务台账</h2>
          </div>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            按独立法人及纳税所属期展示确定性税务结果。项目进销项与项目税负分析请进入项目详情查看。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={onOpenNewRecordModal}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#03b5d3]/20 hover:bg-[#03b5d3]/30 text-[#4cd7f6] text-[13px] font-semibold border border-[#4cd7f6]/40 transition-all cursor-pointer shadow-lg shadow-[#03b5d3]/10"
          >
            <Database className="w-4 h-4" />
            <span>RAG 凭证同步与智能查账</span>
          </button>
          <button
            onClick={onOpenExportModal}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] text-[13px] font-semibold border-t border-[#4cd7f6]/30 transition-all cursor-pointer shadow-md"
          >
            <Download className="w-4 h-4 text-[#4cd7f6]" />
            <span>导出全量台账</span>
          </button>
        </div>
      </div>

      {hasNoRecords && (
        <div className="rounded-xl border border-[#10B981]/30 bg-[#10B981]/5 p-4" role="status" aria-live="polite">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#10B981]" />
            <p className="text-[13px] text-[#c4c5d5] leading-relaxed">{TAX_LEDGER_EMPTY_MESSAGE}</p>
          </div>
        </div>
      )}

      {/* 受控重建操作区 */}
      <div className="glass-panel rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 border border-[#4cd7f6]/20">
        <div>
          <p className="font-semibold text-[#dae2fd]">受控确定性台账生成/重建</p>
          <p className="text-[12px] text-[#c4c5d5] mt-1 leading-relaxed">请先完成 RAG 凭证同步/结构化入库；确认后将原子替换所选期间汇总。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <select
            id="tax-ledger-rebuild-year"
            value={rebuildYear}
            onChange={e => setRebuildYear(e.target.value)}
            disabled={isRebuilding}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            {YEAR_OPTIONS.map(y => (
              <option key={y} value={y}>{y}年</option>
            ))}
          </select>
          <select
            id="tax-ledger-rebuild-month"
            value={rebuildMonth}
            onChange={e => setRebuildMonth(e.target.value)}
            disabled={isRebuilding}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            {MONTH_OPTIONS.map(m => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
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

      {/* 指标卡片 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">法人台账条数</div>
          <div className="text-[18px] font-bold text-[#dae2fd] mt-1 font-mono-num">{filteredRecords.length}</div>
        </div>
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">营业/计税收入</div>
          <div className="text-[18px] font-bold text-[#4cd7f6] mt-1 font-mono-num">¥{totalRevenue.toLocaleString()}</div>
        </div>
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">销项 VAT 合计</div>
          <div className="text-[18px] font-bold text-[#dae2fd] mt-1 font-mono-num">¥{totalOutputVat.toLocaleString()}</div>
        </div>
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">进项 VAT 合计</div>
          <div className="text-[18px] font-bold text-[#dae2fd] mt-1 font-mono-num">¥{totalInputVat.toLocaleString()}</div>
        </div>
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">法人台账应纳 VAT</div>
          <div className="text-[18px] font-bold text-[#10B981] mt-1 font-mono-num">¥{totalVatPayable.toLocaleString()}</div>
          <div className="text-[10px] text-[#8e909f] mt-0.5">按已加载月度汇总</div>
        </div>
        <div className="glass-panel p-3.5 rounded-xl border border-[#444653]/20">
          <div className="text-[11px] text-[#8e909f]">真实成本合计</div>
          <div className="text-[18px] font-bold text-[#dae2fd] mt-1 font-mono-num">¥{totalRealCost.toLocaleString()}</div>
        </div>
      </div>

      {/* 搜索与过滤 */}
      <div className="glass-panel rounded-xl p-3 flex flex-wrap items-center justify-between gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f]" />
          <input
            type="text"
            placeholder="搜索法人主体名称、编码、业务角色..."
            value={searchWord}
            onChange={e => setSearchWord(e.target.value)}
            className="w-full bg-[#171f33]/60 border border-[#444653]/30 rounded-lg pl-9 pr-3 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          />
        </div>
        <div className="flex items-center gap-2">
          <select
            value={filterYear}
            onChange={e => setFilterYear(e.target.value)}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            <option value="全部年份">全部年份</option>
            {YEAR_OPTIONS.map(y => (
              <option key={y} value={y}>{y}年</option>
            ))}
          </select>
          <select
            value={filterMonth}
            onChange={e => setFilterMonth(e.target.value)}
            className="bg-[#171f33] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[13px] text-[#dae2fd] focus:outline-none focus:border-[#4cd7f6]"
          >
            <option value="全部月份">全部月份</option>
            {MONTH_OPTIONS.map(m => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* 台账数据表格 */}
      <div className="glass-panel rounded-xl overflow-hidden border border-[#444653]/30">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-[#171f33]/80 border-b border-[#444653]/40 text-[#8e909f]">
              <tr>
                <th 
                  onClick={() => handleSort('entityName')}
                  className="py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center gap-1.5">
                    <span>法人主体</span>
                    {sortField === 'entityName' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('period')}
                  className="py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center gap-1.5">
                    <span>所属期</span>
                    {sortField === 'period' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('revenue')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>营业/计税收入</span>
                    {sortField === 'revenue' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('outputVat')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>销项 VAT</span>
                    {sortField === 'outputVat' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('inputVat')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>进项 VAT</span>
                    {sortField === 'inputVat' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('vatPayable')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>应纳 VAT</span>
                    {sortField === 'vatPayable' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('realCost')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>真实成本</span>
                    {sortField === 'realCost' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('estimatedProfit')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>预计利润</span>
                    {sortField === 'estimatedProfit' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('estimatedCit')}
                  className="py-2.5 px-3 text-right cursor-pointer hover:text-[#4cd7f6] select-none transition-colors"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <span>预计所得税</span>
                    {sortField === 'estimatedCit' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3 h-3 text-[#4cd7f6]" /> : <ArrowDown className="w-3 h-3 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40" />
                    )}
                  </div>
                </th>
                <th className="py-2.5 px-3 text-center">状态</th>
                <th className="py-2.5 px-3 text-center">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {sortedRecords.length === 0 ? (
                <tr>
                  <td colSpan={11} className="py-8 text-center text-[#8e909f]">
                    没有匹配的法人台账记录。
                  </td>
                </tr>
              ) : (
                sortedRecords.map(rec => (
                  <tr key={rec.id} className="hover:bg-[#222a3d]/50 transition-colors">
                    <td className="py-2.5 px-3">
                      <div className="font-semibold text-[#dae2fd]">{rec.entityName}</div>
                      <div className="text-[11px] text-[#8e909f] font-mono-num">
                        {rec.entityCode} {rec.businessRole ? `· ${rec.businessRole}` : ''}
                      </div>
                    </td>
                    <td className="py-2.5 px-3 font-mono-num text-[#c4c5d5] whitespace-nowrap">
                      {rec.period}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#4cd7f6]">
                      ¥{rec.revenue.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">
                      ¥{rec.outputVat.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#dae2fd]">
                      ¥{rec.inputVat.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num font-semibold text-[#10B981]">
                      ¥{rec.vatPayable.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#c4c5d5]">
                      ¥{rec.realCost.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#c4c5d5]">
                      ¥{rec.estimatedProfit.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-num text-[#c4c5d5]">
                      ¥{rec.estimatedCit.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${
                        rec.generated
                          ? 'bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/30'
                          : 'bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30'
                      }`}>
                        {rec.generated ? '已生成' : '待生成'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <button
                        type="button"
                        onClick={() => onAskAiAboutRisk(rec.entityName)}
                        className="text-[12px] text-[#4cd7f6] hover:underline"
                      >
                        AI研判
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
