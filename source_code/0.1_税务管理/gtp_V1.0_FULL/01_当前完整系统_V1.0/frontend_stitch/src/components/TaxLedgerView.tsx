import { useState } from 'react';
import { 
  ReceiptText, 
  Search, 
  Filter, 
  Download, 
  Database,
  CheckCircle2, 
  AlertTriangle, 
  AlertOctagon, 
  Building,
  FileSpreadsheet,
  Layers,
  Sparkles,
  ArrowUpDown,
  ArrowUp,
  ArrowDown
} from 'lucide-react';
import { DataStatus, ProjectItem, TaxLedgerRecord, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';

type SortField = 'projectName' | 'entityName' | 'declareAmount' | 'taxCategory' | 'status' | 'flow' | null;
type SortOrder = 'asc' | 'desc';

export const TAX_LEDGER_EMPTY_MESSAGE = '接口正常、指定期间暂无已生成台账。请先完成 RAG 凭证同步/结构化入库，再由受控确定性重建生成。';

interface TaxLedgerViewProps {
  projects: ProjectItem[];
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
  projects,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onOpenNewRecordModal,
  onOpenExportModal,
  onAskAiAboutRisk,
  onRebuildTaxLedger,
  isRebuilding,
  settings
}: TaxLedgerViewProps) {
  const now = new Date();
  const defaultYear = String(now.getFullYear());
  const defaultMonth = String(now.getMonth() + 1).padStart(2, '0');

  const [searchWord, setSearchWord] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('全部税种');
  const [selectedRisk, setSelectedRisk] = useState('全部风险');
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
      setSortOrder(field === 'declareAmount' ? 'desc' : 'asc');
    }
  };

  const isAutoFourFlows = settings?.autoFourFlowsMatch ?? true;

  // 聚合所有项目的税务台账
  const allRecords: (TaxLedgerRecord & { projectName: string; projectCode: string })[] = projects.flatMap(p => 
    p.taxRecords.map(r => ({ ...r, projectName: p.name, projectCode: p.projectCode }))
  );
  const hasNoRecords = allRecords.length === 0;

  const filteredRecords = allRecords.filter(item => {
    const matchesSearch = 
      item.entityName.toLowerCase().includes(searchWord.toLowerCase()) ||
      item.projectName.toLowerCase().includes(searchWord.toLowerCase()) ||
      (item.invoiceCode && item.invoiceCode.toLowerCase().includes(searchWord.toLowerCase()));
    
    const matchesCat = selectedCategory === '全部税种' || item.taxCategory.includes(selectedCategory);
    const matchesRisk = selectedRisk === '全部风险' || item.riskLevel === selectedRisk;
    const matchesYear = filterYear === '全部年份' || item.filingPeriod.startsWith(filterYear);
    const matchesMonth = filterMonth === '全部月份' || item.filingPeriod.endsWith(`-${filterMonth}`);

    return matchesSearch && matchesCat && matchesRisk && matchesYear && matchesMonth;
  });

  const sortedRecords = [...filteredRecords].sort((a, b) => {
    if (!sortField) return 0;

    let res = 0;
    if (sortField === 'projectName') {
      res = a.projectName.localeCompare(b.projectName, 'zh-CN');
    } else if (sortField === 'entityName') {
      res = a.entityName.localeCompare(b.entityName, 'zh-CN');
    } else if (sortField === 'declareAmount') {
      res = a.declareAmount - b.declareAmount;
    } else if (sortField === 'taxCategory') {
      res = a.taxCategory.localeCompare(b.taxCategory, 'zh-CN');
    } else if (sortField === 'status') {
      res = a.status.localeCompare(b.status, 'zh-CN');
    } else if (sortField === 'flow') {
      const aFlow = Object.values(a.fourFlowsCheck).every(Boolean) ? 1 : 0;
      const bFlow = Object.values(b.fourFlowsCheck).every(Boolean) ? 1 : 0;
      res = aFlow - bFlow;
    }

    return sortOrder === 'asc' ? res : -res;
  });

  const totalDeclare = filteredRecords.reduce((acc, cur) => acc + cur.declareAmount, 0);
  const totalTax = filteredRecords.reduce((acc, cur) => acc + cur.taxAmount, 0);

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
        <h2 className="text-[28px] font-bold text-[#dae2fd]">工程全周期税务台账</h2>
        <DataStatusCard status={dataStatus} title="税务台账不可用" message={dataStatusMessage} onRetry={onRetry} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 顶部标题与汇总 */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight">工程全周期税务台账与查账中枢</h2>
          </div>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            当前展示 Tax 服务返回的确定性税务台账；RAG 凭证同步用于补充原始凭证、证据和待复核信息。
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

      <div className="glass-panel rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 border border-[#4cd7f6]/20">
        <div>
          <p className="font-semibold text-[#dae2fd]">受控确定性台账生成/重建</p>
          <p className="text-[12px] text-[#c4c5d5] mt-1 leading-relaxed">请先完成 RAG 凭证同步/结构化入库；确认后将原子替换所选期间汇总。</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <label htmlFor="tax-ledger-rebuild-year" className="text-[12px] text-[#8e909f]">所属期间：</label>
          <select
            id="tax-ledger-rebuild-year"
            value={rebuildYear}
            onChange={event => setRebuildYear(event.target.value)}
            className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            disabled={isRebuilding}
          >
            {YEAR_OPTIONS.map(y => (
              <option key={y} value={y}>{y}年</option>
            ))}
          </select>
          <select
            id="tax-ledger-rebuild-month"
            value={rebuildMonth}
            onChange={event => setRebuildMonth(event.target.value)}
            className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            disabled={isRebuilding}
          >
            {MONTH_OPTIONS.map(m => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleRebuild}
            disabled={isRebuilding || !/^(?:\d{4})-(?:0[1-9]|1[0-2])$/.test(rebuildPeriod)}
            className="px-3.5 py-2 rounded-lg bg-[#1e40af] hover:bg-[#1e40af]/80 disabled:opacity-50 disabled:cursor-not-allowed text-[#dde1ff] text-[12px] font-semibold border border-[#4cd7f6]/30 transition-all cursor-pointer shadow-md"
          >
            {isRebuilding ? '正在生成…' : '生成/重建台账'}
          </button>
        </div>
      </div>

      {/* 统计指标小卡 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="glass-panel rounded-xl p-4 glow-cyan">
          <p className="text-[11px] font-bold text-[#8e909f] uppercase">当前筛选记录数</p>
          <p className="text-[24px] font-bold font-mono-num text-[#dae2fd] mt-1">{filteredRecords.length} 笔凭证</p>
        </div>
        <div className="glass-panel rounded-xl p-4 glow-cyan">
          <p className="text-[11px] font-bold text-[#8e909f] uppercase">累计计税申报金额</p>
          <p className="text-[24px] font-bold font-mono-num text-[#b8c4ff] mt-1">¥ {totalDeclare.toLocaleString('zh-CN')} 元</p>
        </div>
        <div className="glass-panel rounded-xl p-4 glow-cyan">
          <p className="text-[11px] font-bold text-[#8e909f] uppercase">当期应纳与代扣税额</p>
          <p className="text-[24px] font-bold font-mono-num text-[#4cd7f6] mt-1">¥ {totalTax.toLocaleString('zh-CN')} 元</p>
        </div>
      </div>

      {/* 筛选与搜索工具栏 */}
      <div className="glass-panel rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-3 flex-1">
          <div className="relative min-w-[220px]">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f]" />
            <input
              type="text"
              value={searchWord}
              onChange={(e) => setSearchWord(e.target.value)}
              placeholder="按实体名称、所属项目或发票号检索..."
              className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg pl-9 pr-3 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none font-mono-num"
            />
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[#8e909f]">所属年份：</span>
            <select
              value={filterYear}
              onChange={(e) => setFilterYear(e.target.value)}
              className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            >
              <option value="全部年份">全部年份</option>
              {YEAR_OPTIONS.map(y => (
                <option key={y} value={y}>{y}年</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[#8e909f]">所属月份：</span>
            <select
              value={filterMonth}
              onChange={(e) => setFilterMonth(e.target.value)}
              className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            >
              <option value="全部月份">全部月份</option>
              {MONTH_OPTIONS.map(m => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[#8e909f]">税种：</span>
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
              className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            >
              <option value="全部税种">全部税种</option>
              <option value="增值税">增值税</option>
              <option value="企业所得税">企业所得税</option>
              <option value="预提所得税">预提所得税</option>
              <option value="关税">关税及进口税</option>
              <option value="城市维护建设税">城建税及附加</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[#8e909f]">风险等级：</span>
            <select
              value={selectedRisk}
              onChange={(e) => setSelectedRisk(e.target.value)}
              className="bg-[#131b2e] border border-[#444653]/40 rounded-lg px-2.5 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none cursor-pointer"
            >
              <option value="全部风险">全部风险</option>
              <option value="正常">正常</option>
              <option value="预警">预警</option>
              <option value="高危">高危稽查</option>
            </select>
          </div>
        </div>

      </div>

      {/* 台账明细表 */}
      <div className="glass-panel rounded-xl p-5 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed text-left border-collapse text-[12.5px] font-mono-num">
            <colgroup>
              <col style={{ width: '20%' }} />
              <col style={{ width: '20%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '17%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '8%' }} />
            </colgroup>
            <thead>
              <tr className="border-b border-[#444653]/30 text-[12px] font-semibold text-[#8e909f]">
                <th 
                  onClick={() => handleSort('projectName')}
                  className="py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按项目名称排序"
                >
                  <div className="flex items-center gap-1.5">
                    <div>
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6] truncate">所属工程项目</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">工程编码</div>
                    </div>
                    {sortField === 'projectName' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('entityName')}
                  className="py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按实体名称排序"
                >
                  <div className="flex items-center gap-1.5">
                    <div>
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6] truncate">实体名称</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">标段分类</div>
                    </div>
                    {sortField === 'entityName' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th className="py-2.5 px-2 text-center whitespace-nowrap">
                  <div>归属属性</div>
                  <div className="text-[10px] text-[#8e909f]/70 font-normal">内部/外部</div>
                </th>
                <th 
                  onClick={() => handleSort('declareAmount')}
                  className="py-2.5 px-3 text-right whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按金额/税额排序"
                >
                  <div className="flex items-center justify-end gap-1.5">
                    <div className="text-right">
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6]">申报计税</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">应纳税额</div>
                    </div>
                    {sortField === 'declareAmount' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('taxCategory')}
                  className="py-2.5 px-3 cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按税种/所属期排序"
                >
                  <div className="flex items-center gap-1.5">
                    <div>
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6] truncate">税种类别</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">税款所属期</div>
                    </div>
                    {sortField === 'taxCategory' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('status')}
                  className="py-2.5 px-2 text-center whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按审核状态排序"
                >
                  <div className="flex items-center justify-center gap-1.5">
                    <div>
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6]">审核状态</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">入库/复核</div>
                    </div>
                    {sortField === 'status' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('flow')}
                  className="py-2.5 px-2 text-center whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按四流合一合规性排序"
                >
                  <div className="flex items-center justify-center gap-1.5">
                    <div>
                      <div className="text-[12px] text-[#8e909f] group-hover:text-[#4cd7f6]">四流合一</div>
                      <div className="text-[10px] text-[#8e909f]/70 font-normal">核验结果</div>
                    </div>
                    {sortField === 'flow' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity flex-shrink-0" />
                    )}
                  </div>
                </th>
                <th className="py-2.5 px-2 text-center whitespace-nowrap">
                  <div>操作</div>
                  <div className="text-[10px] text-[#8e909f]/70 font-normal">AI研判</div>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {sortedRecords.map((rec) => {
                const isHighRisk = rec.riskLevel === '高危';
                const isMedium = rec.riskLevel === '预警';
                const isFlowOk = Object.values(rec.fourFlowsCheck).every(Boolean);

                return (
                  <tr 
                    key={rec.id}
                    className={`hover:bg-[#222a3d]/50 transition-colors ${
                      isHighRisk ? 'bg-[#EF4444]/10 border-l-2 border-[#EF4444]' : isMedium ? 'bg-[#F59E0B]/5' : ''
                    }`}
                  >
                    {/* 所属工程项目 */}
                    <td className="py-2 px-3">
                      <div className="font-bold text-[#dae2fd] text-[13px] leading-snug break-words whitespace-normal" title={rec.projectName}>
                        {rec.projectName}
                      </div>
                      <div className="text-[11px] text-[#8e909f] font-mono-num mt-0.5">
                        {rec.projectCode}
                      </div>
                    </td>

                    {/* 实体名称与标段分类 */}
                    <td className="py-2 px-3">
                      <div className={`font-semibold text-[13px] leading-snug break-words whitespace-normal ${isHighRisk ? 'text-[#ffb4ab]' : 'text-[#dde1ff]'}`} title={rec.entityName}>
                        {rec.entityName}
                      </div>
                      <div className="text-[11px] text-[#8e909f] break-words whitespace-normal mt-0.5" title={rec.entityCategory}>
                        {rec.entityCategory}
                      </div>
                    </td>

                    {/* 归属属性：系统内 / 系统外 */}
                    <td className="py-2 px-2 text-center whitespace-nowrap">
                      {(() => {
                        const isExt = rec.entityName.includes('EXT-') || rec.entityName.includes('外部') || rec.entityName.includes('系统外') || rec.isInternal === false;

                        return !isExt ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30 font-medium">
                            🏢 内部
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#a78bfa] bg-[#8b5cf6]/15 px-2 py-0.5 rounded border border-[#8b5cf6]/30 font-medium">
                            🌐 外部
                          </span>
                        );
                      })()}
                    </td>

                    {/* 申报计税金额与应纳/预提税额 */}
                    <td className="py-2 px-3 text-right whitespace-nowrap">
                      <div className="font-bold text-[#dae2fd] text-[13px]">
                        ¥ {rec.declareAmount.toLocaleString('zh-CN')}
                      </div>
                      <div className="text-[11.5px] font-semibold text-[#4cd7f6] mt-0.5">
                        税: ¥ {rec.taxAmount.toLocaleString('zh-CN')}
                      </div>
                    </td>

                    {/* 税种类别与所属期 */}
                    <td className="py-2 px-3">
                      <div className="text-[#c4c5d5] text-[12px] font-medium leading-snug break-words whitespace-normal">
                        {rec.taxCategory}
                      </div>
                      <div className="text-[11px] text-[#8e909f] mt-0.5">
                        {rec.filingPeriod}
                      </div>
                    </td>

                    {/* 审核状态 */}
                    <td className="py-2 px-2 text-center whitespace-nowrap">
                      <span className={`text-[11px] font-bold px-2 py-0.5 rounded ${
                        isHighRisk 
                          ? 'text-[#EF4444] bg-[#EF4444]/15 border border-[#EF4444]/30' 
                          : isMedium 
                          ? 'text-[#F59E0B] bg-[#F59E0B]/15 border border-[#F59E0B]/30' 
                          : 'text-[#10B981] bg-[#10B981]/15 border border-[#10B981]/30'
                      }`}>
                        {rec.status}
                      </span>
                    </td>

                    {/* 四流核验 */}
                    <td className="py-2 px-2 text-center whitespace-nowrap">
                      <span className={`inline-flex items-center justify-center whitespace-nowrap text-[11px] px-2 py-0.5 rounded border font-medium ${
                        isFlowOk ? 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30' : 'bg-[#EF4444]/20 text-[#EF4444] border-[#EF4444]/40 font-bold animate-pulse'
                      }`}>
                        {isFlowOk ? '合规' : '差异'}
                      </span>
                    </td>

                    {/* 操作动作 */}
                    <td className="py-2 px-2 text-center whitespace-nowrap">
                      <button
                        onClick={() => onAskAiAboutRisk(rec.entityName)}
                        className="px-2.5 py-1 text-[11px] font-semibold bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] rounded border border-[#4cd7f6]/30 cursor-pointer transition-colors"
                      >
                        AI研判
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
