import { useState } from 'react';
import { 
  Building2, 
  Download, 
  Database,
  PlusCircle, 
  ShieldAlert, 
  ShieldCheck, 
  AlertTriangle, 
  ArrowLeft, 
  Search, 
  Filter, 
  FileCheck2, 
  Info,
  ChevronDown,
  ExternalLink,
  CheckCircle2,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Compass
} from 'lucide-react';
import { ProjectItem, TaxLedgerRecord, CostBreakdownItem, SystemSettings } from '../types';

type SortField = 'entityName' | 'declareAmount' | 'taxCategory' | 'status' | 'flow' | null;
type SortOrder = 'asc' | 'desc';

interface ProjectDetailViewProps {
  project: ProjectItem;
  projects?: ProjectItem[];
  onSelectProject?: (projectId: string) => void;
  onBack: () => void;
  onOpenNewRecordModal: () => void;
  onOpenExportModal: () => void;
  onAskAiAboutRisk: (entityName: string) => void;
  onGoToPlanning?: () => void;
  settings?: SystemSettings;
}

export function ProjectDetailView({
  project,
  projects,
  onSelectProject,
  onBack,
  onOpenNewRecordModal,
  onOpenExportModal,
  onAskAiAboutRisk,
  onGoToPlanning,
  settings
}: ProjectDetailViewProps) {
  const [selectedRecordForDetail, setSelectedRecordForDetail] = useState<TaxLedgerRecord | null>(null);
  const [filterRisk, setFilterRisk] = useState<string>('全部');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder(field === 'declareAmount' ? 'desc' : 'asc');
    }
  };

  const stopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;

  const filteredTaxRecords = project.taxRecords.filter(rec => {
    if (filterRisk === '全部') return true;
    return rec.riskLevel === filterRisk;
  });

  const sortedTaxRecords = [...filteredTaxRecords].sort((a, b) => {
    if (!sortField) return 0;

    let res = 0;
    if (sortField === 'entityName') {
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

  return (
    <div className="space-y-6">
      {/* 顶部导航返回与项目操作 */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-2 border-b border-[#444653]/30">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-[13px] font-semibold text-[#4cd7f6] hover:text-[#dae2fd] transition-colors cursor-pointer bg-[#03b5d3]/10 px-3 py-1.5 rounded-lg border border-[#4cd7f6]/30"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>返回项目工程库 (5大标杆工程)</span>
          </button>

          {/* 工程快速切换选择器 */}
          {projects && projects.length > 0 && onSelectProject && (
            <div className="flex items-center gap-2 text-[12px]">
              <span className="text-[#8e909f] hidden sm:inline">切换标段:</span>
              <select
                value={project.id}
                onChange={e => onSelectProject(e.target.value)}
                className="bg-[#131b2e] border border-[#444653]/50 text-[#dae2fd] text-[12px] font-semibold rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#4cd7f6]/60 cursor-pointer max-w-[260px] truncate"
              >
                {projects.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.projectCode} · {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (onGoToPlanning) {
                onGoToPlanning();
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#8b5cf6]/20 hover:bg-[#8b5cf6]/30 text-[#c4b5fd] text-[12px] font-semibold rounded-lg border border-[#a78bfa]/40 transition-all cursor-pointer shadow-[0_0_10px_rgba(139,92,246,0.2)]"
            title="进入两层财税筹划沙盘与确定性计算引擎"
          >
            <Compass className="w-3.5 h-3.5 text-[#a78bfa]" />
            <span>🧭 财税筹划沙盘</span>
          </button>
          <button
            onClick={onOpenNewRecordModal}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#03b5d3]/20 hover:bg-[#03b5d3]/30 text-[#4cd7f6] text-[12px] font-semibold rounded-lg border border-[#4cd7f6]/40 transition-all cursor-pointer"
          >
            <Database className="w-3.5 h-3.5" />
            <span>RAG 知识库检索同步</span>
          </button>
          <button
            onClick={onOpenExportModal}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] text-[12px] font-semibold rounded-lg border border-[#4cd7f6]/30 transition-all cursor-pointer"
          >
            <Download className="w-3.5 h-3.5 text-[#4cd7f6]" />
            <span>导出项目专报</span>
          </button>
        </div>
      </div>

      {/* 模块 1: 项目财务总览指标卡 (还原 Image 1 顶部看板) */}
      <section className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden">
        {/* 背景氛围晕染 */}
        <div className="absolute -right-20 -top-20 w-64 h-64 bg-[#4cd7f6]/5 rounded-full blur-3xl pointer-events-none"></div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <h3 className="text-[22px] font-bold text-[#dae2fd] flex items-center gap-2.5">
              <Building2 className="w-6 h-6 text-[#4cd7f6]" />
              <span>项目概况: {project.name}</span>
            </h3>
            <p className="text-[13px] text-[#c4c5d5] mt-1">
              工程编号: <span className="font-mono-num text-[#dae2fd]">{project.projectCode}</span> | 财务阶段: <span className="text-[#4cd7f6] font-semibold">{project.constructionStage}</span>
            </p>
          </div>
          <div className="flex items-center gap-2 bg-[#171f33] px-3.5 py-1.5 rounded-full border border-[#444653]/40">
            <span className="w-2.5 h-2.5 rounded-full bg-[#10B981] shadow-[0_0_8px_#10B981]"></span>
            <span className="text-[12px] font-bold text-[#dae2fd]">健康评级: {project.healthGrade}</span>
          </div>
        </div>

        {/* 三栏金额核心指标 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div className="bg-[#0b1326]/60 rounded-xl p-4 border border-[#444653]/30">
            <p className="text-[11px] font-bold text-[#8e909f] uppercase tracking-wider">总预算 (已批复)</p>
            <p className="text-[22px] font-bold font-mono-num text-[#dae2fd] mt-1">
              ¥ {project.totalBudget.toLocaleString('zh-CN')} 元
            </p>
          </div>
          <div className="bg-[#0b1326]/60 rounded-xl p-4 border border-[#444653]/30">
            <p className="text-[11px] font-bold text-[#8e909f] uppercase tracking-wider">累计已付款项</p>
            <p className="text-[22px] font-bold font-mono-num text-[#b8c4ff] mt-1">
              ¥ {project.spentAmount.toLocaleString('zh-CN')} 元
            </p>
          </div>
          <div className="bg-[#0b1326]/60 rounded-xl p-4 border border-[#444653]/30">
            <p className="text-[11px] font-bold text-[#8e909f] uppercase tracking-wider">剩余可用预算</p>
            <p className="text-[22px] font-bold font-mono-num text-[#4cd7f6] mt-1">
              ¥ {project.remainingBudget.toLocaleString('zh-CN')} 元
            </p>
          </div>
        </div>

        {/* 资金消耗进度条 */}
        <div className="w-full">
          <div className="flex justify-between text-[12px] font-mono-num mb-2 text-[#c4c5d5]">
            <span>资金消耗进度</span>
            <span className="text-[#4cd7f6] font-bold">{project.progressPercent}%</span>
          </div>
          <div className="h-2.5 w-full bg-[#2d3449] rounded-full overflow-hidden flex">
            <div 
              className="h-full bg-gradient-to-r from-[#1e40af] to-[#4cd7f6] shadow-[0_0_12px_#4cd7f6] rounded-full transition-all duration-500" 
              style={{ width: `${project.progressPercent}%` }}
            ></div>
          </div>
        </div>
      </section>

      {/* 模块 2: 税务台账列表 (还原 Image 1 中部表格) */}
      <section className="glass-panel rounded-xl p-5 flex flex-col">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[#444653]/30 mb-4">
          <div className="flex items-center gap-3">
            <h3 className="text-[17px] font-bold text-[#dae2fd]">税务台账: 各分包与供应商实体明细</h3>
            <div className="flex items-center gap-1 bg-[#131b2e] px-2 py-1 rounded border border-[#444653]/30 text-[11px]">
              <Filter className="w-3 h-3 text-[#8e909f]" />
              <select
                value={filterRisk}
                onChange={(e) => setFilterRisk(e.target.value)}
                className="bg-transparent text-[#dae2fd] focus:outline-none cursor-pointer"
              >
                <option value="全部" className="bg-[#171f33]">全部风险</option>
                <option value="正常" className="bg-[#171f33]">正常</option>
                <option value="预警" className="bg-[#171f33]">预警</option>
                <option value="高危" className="bg-[#171f33]">高危稽查</option>
              </select>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={onOpenExportModal}
              className="text-[12px] font-medium text-[#4cd7f6] hover:text-[#dae2fd] transition-colors flex items-center gap-1 cursor-pointer"
            >
              <span>导出表格</span>
              <Download className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* 表格容器 */}
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-[#444653]/30 text-[12px] font-semibold text-[#8e909f]">
                <th 
                  onClick={() => handleSort('entityName')}
                  className="py-2.5 px-3 w-[240px] min-w-[240px] max-w-[240px] cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按实体名称排序"
                >
                  <div className="flex items-center gap-1">
                    <span>实体名称 / 标段分类</span>
                    {sortField === 'entityName' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th className="py-2.5 px-2 text-center whitespace-nowrap w-[90px] min-w-[90px]">
                  <span>归属属性</span>
                </th>
                <th 
                  onClick={() => handleSort('declareAmount')}
                  className="py-2.5 px-3 text-right whitespace-nowrap w-[150px] min-w-[150px] cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按金额/税额排序"
                >
                  <div className="flex items-center justify-end gap-1">
                    <span>申报计税 / 应纳税额</span>
                    {sortField === 'declareAmount' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('taxCategory')}
                  className="py-2.5 px-3 w-[200px] min-w-[200px] max-w-[200px] cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按税种/周期排序"
                >
                  <div className="flex items-center gap-1">
                    <span>税种 / 申报周期</span>
                    {sortField === 'taxCategory' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('status')}
                  className="py-2.5 px-2 text-center whitespace-nowrap w-[110px] min-w-[110px] cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按审核状态排序"
                >
                  <div className="flex items-center justify-center gap-1">
                    <span>审核状态</span>
                    {sortField === 'status' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('flow')}
                  className="py-2.5 px-2 text-center whitespace-nowrap w-[100px] min-w-[100px] cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按四流合一合规性排序"
                >
                  <div className="flex items-center justify-center gap-1">
                    <span>四流合一</span>
                    {sortField === 'flow' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th className="py-2.5 px-3 text-right whitespace-nowrap w-[80px]">操作</th>
              </tr>
            </thead>
            <tbody className="text-[13px] font-mono-num divide-y divide-[#444653]/20">
              {sortedTaxRecords.map((rec) => {
                const isHighRisk = rec.riskLevel === '高危';
                const isMediumRisk = rec.riskLevel === '预警';
                const allFlowsOk = Object.values(rec.fourFlowsCheck).every(Boolean);

                return (
                  <tr 
                    key={rec.id}
                    className={`transition-colors duration-150 ${
                      isHighRisk 
                        ? 'bg-[#EF4444]/10 hover:bg-[#EF4444]/20 border-l-2 border-[#EF4444]' 
                        : isMediumRisk
                        ? 'bg-[#F59E0B]/5 hover:bg-[#F59E0B]/15 border-l-2 border-[#F59E0B]'
                        : 'hover:bg-[#222a3d]/50'
                    }`}
                  >
                    {/* 实体名称与标段分类（固定宽度，自动换行） */}
                    <td className="py-2.5 px-3 w-[240px] min-w-[240px] max-w-[240px]">
                      <div className={`font-semibold text-[13px] leading-snug break-words whitespace-normal ${isHighRisk ? 'text-[#ffb4ab]' : 'text-[#dae2fd]'}`} title={rec.entityName}>
                        {rec.entityName}
                      </div>
                      <div className="text-[11px] text-[#8e909f] break-words whitespace-normal mt-0.5" title={rec.entityCategory}>
                        {rec.entityCategory}
                      </div>
                    </td>

                    {/* 归属属性：系统内 / 系统外 */}
                    <td className="py-2.5 px-2 text-center whitespace-nowrap w-[90px] min-w-[90px]">
                      {(() => {
                        const isExt = rec.entityName.includes('EXT-') || rec.entityName.includes('外部') || rec.entityName.includes('系统外') || rec.isInternal === false;

                        return !isExt ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30 font-medium">
                            🏢 系统内
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#a78bfa] bg-[#8b5cf6]/15 px-2 py-0.5 rounded border border-[#8b5cf6]/30 font-medium">
                            🌐 系统外
                          </span>
                        );
                      })()}
                    </td>

                    {/* 申报计税金额与应纳税额 */}
                    <td className="py-2.5 px-3 text-right whitespace-nowrap w-[150px] min-w-[150px]">
                      <div className="font-bold text-[#dae2fd] text-[13px]">
                        ¥ {rec.declareAmount.toLocaleString('zh-CN')}
                      </div>
                      <div className="text-[11.5px] font-semibold text-[#4cd7f6] mt-0.5">
                        税额: ¥ {rec.taxAmount.toLocaleString('zh-CN')}
                      </div>
                    </td>

                    {/* 税种类别与所属期（固定宽度，自动换行） */}
                    <td className="py-2.5 px-3 w-[200px] min-w-[200px] max-w-[200px]">
                      <div className="text-[#c4c5d5] text-[12px] font-medium leading-snug break-words whitespace-normal">
                        {rec.taxCategory}
                      </div>
                      <div className="text-[11px] text-[#8e909f] mt-0.5">
                        {rec.filingPeriod}
                      </div>
                    </td>

                    {/* 审核状态 */}
                    <td className="py-2.5 px-2 text-center whitespace-nowrap">
                      <span className={`text-[11.5px] font-bold px-2 py-0.5 rounded ${
                        isHighRisk 
                          ? 'text-[#EF4444] bg-[#EF4444]/15 border border-[#EF4444]/30' 
                          : isMediumRisk 
                          ? 'text-[#F59E0B] bg-[#F59E0B]/15 border border-[#F59E0B]/30' 
                          : 'text-[#10B981] bg-[#10B981]/15 border border-[#10B981]/30'
                      }`}>
                        {rec.status}
                      </span>
                    </td>

                    {/* 四流合一核验 */}
                    <td className="py-2.5 px-2 text-center whitespace-nowrap">
                      {allFlowsOk ? (
                        <span className="inline-flex items-center gap-1 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2.5 py-0.5 rounded border border-[#10B981]/30 font-medium">
                          <CheckCircle2 className="w-3 h-3" /> 完全合规
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] text-[#EF4444] bg-[#EF4444]/20 px-2.5 py-0.5 rounded border border-[#EF4444]/40 font-bold animate-pulse">
                          <AlertTriangle className="w-3 h-3" /> 存在差异
                        </span>
                      )}
                    </td>

                    {/* 操作动作 */}
                    <td className="py-2.5 px-3 text-right space-x-1.5 whitespace-nowrap">
                      {isHighRisk && (
                        <button
                          onClick={() => onAskAiAboutRisk(rec.entityName)}
                          className="px-2 py-0.5 bg-[#EF4444]/20 hover:bg-[#EF4444]/30 text-[#ffb4ab] text-[11px] font-bold rounded border border-[#EF4444]/50 cursor-pointer transition-colors"
                        >
                          智能诊断
                        </button>
                      )}
                      <button
                        onClick={() => setSelectedRecordForDetail(rec)}
                        className="px-2 py-0.5 bg-[#222a3d] hover:bg-[#334155] text-[#4cd7f6] rounded border border-[#4cd7f6]/30 text-[11px] cursor-pointer transition-colors"
                      >
                        详情
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* 模块 3: 成本渗透率矩阵 (工作任务分解结构 - 还原 Image 1 底部表格) */}
      <section className="glass-panel rounded-xl p-5">
        <div className="flex justify-between items-center mb-4 pb-3 border-b border-[#444653]/30">
          <div>
            <h3 className="text-[17px] font-bold text-[#dae2fd]">成本渗透率矩阵 (工作任务分解结构)</h3>
            <p className="text-[12px] text-[#c4c5d5]">逐级穿透工程科目预算计划、实际发生成本与偏差率</p>
          </div>
        </div>

        <div className="bg-[#131b2e]/90 rounded-xl border border-[#444653]/30 overflow-x-auto">
          <table className="w-full text-left border-collapse text-[13px] font-mono-num min-w-[760px]">
            <thead className="bg-[#171f33] text-[12px] font-semibold text-[#8e909f]">
              <tr>
                <th className="py-3 px-4 whitespace-nowrap">成本科目 (工作任务分解编码)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">计划预算 (计划值)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">实际发生成本 (实际值)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">
                  <span>偏差率 </span>
                  <span className="text-[10px] text-[#ffb59a] font-normal">(止付线: +{stopPayThreshold}%)</span>
                </th>
                <th className="py-3 px-4 whitespace-nowrap">责任工程师</th>
                <th className="py-3 px-4 text-center whitespace-nowrap">状态 / 管控指令</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {project.costItems.map((item) => {
                const isOverThreshold = item.variancePercent >= stopPayThreshold;
                const isWarning = item.status === '超支预警' || isOverThreshold;
                const isSaved = item.status === '节约支出';

                return (
                  <tr 
                    key={item.id}
                    className={`hover:bg-[#222a3d]/40 transition-colors ${
                      item.level === 2 ? 'bg-[#171f33]/30' : 'font-semibold'
                    } ${isOverThreshold ? 'bg-[#EF4444]/5' : ''}`}
                  >
                    <td className={`py-3 px-4 whitespace-nowrap ${item.level === 2 ? 'pl-8 text-[#dae2fd]' : 'text-[#dde1ff]'}`}>
                      {item.code} {item.level === 1 ? `(${item.name})` : ''}
                    </td>
                    <td className="py-3 px-4 text-right text-[#8e909f] whitespace-nowrap">
                      ¥ {item.plannedAmount.toLocaleString('zh-CN')}
                    </td>
                    <td className="py-3 px-4 text-right text-[#dae2fd] whitespace-nowrap">
                      ¥ {item.actualAmount.toLocaleString('zh-CN')}
                    </td>
                    <td className={`py-3 px-4 text-right font-bold whitespace-nowrap ${
                      isWarning ? 'text-[#EF4444]' : isSaved ? 'text-[#10B981]' : 'text-[#4cd7f6]'
                    }`}>
                      {item.variancePercent > 0 ? `+${item.variancePercent}%` : item.variancePercent === 0 ? '持平' : `${item.variancePercent}%`}
                    </td>
                    <td className="py-3 px-4 text-[#c4c5d5] whitespace-nowrap">
                      {item.manager}
                    </td>
                    <td className="py-3 px-4 text-center whitespace-nowrap">
                      <span className={`text-[11px] px-2 py-0.5 rounded border inline-flex items-center gap-1 whitespace-nowrap ${
                        isOverThreshold
                          ? 'bg-[#EF4444]/20 text-[#ffb4ab] border-[#EF4444]/40 font-bold animate-pulse'
                          : isWarning 
                          ? 'bg-[#EF4444]/15 text-[#EF4444] border-[#EF4444]/30' 
                          : isSaved 
                          ? 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30' 
                          : 'bg-[#03b5d3]/15 text-[#4cd7f6] border-[#4cd7f6]/30'
                      }`}>
                        {isOverThreshold ? `【已触发止付令】` : item.status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* 弹窗：单条税务记录详情与四流合一核查穿透 */}
      {selectedRecordForDetail && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#171f33] border border-[#4cd7f6]/50 rounded-xl max-w-xl w-full p-6 shadow-2xl space-y-4">
            <div className="flex justify-between items-start pb-3 border-b border-[#444653]/40">
              <div>
                <h4 className="text-[17px] font-bold text-[#dae2fd]">{selectedRecordForDetail.entityName}</h4>
                <p className="text-[12px] text-[#4cd7f6]">{selectedRecordForDetail.taxCategory} · {selectedRecordForDetail.filingPeriod}</p>
              </div>
              <button 
                onClick={() => setSelectedRecordForDetail(null)}
                className="text-[#8e909f] hover:text-[#dae2fd] text-[18px] cursor-pointer"
              >
                ✕
              </button>
            </div>

            {/* 核心涉税信息 */}
            <div className="grid grid-cols-2 gap-3 text-[13px] font-mono-num bg-[#131b2e] p-3 rounded-lg border border-[#444653]/30">
              <div>
                <span className="text-[#8e909f]">申报计税金额:</span>
                <p className="text-[#dae2fd] font-bold">¥ {selectedRecordForDetail.declareAmount.toLocaleString('zh-CN')} 元</p>
              </div>
              <div>
                <span className="text-[#8e909f]">应纳税额:</span>
                <p className="text-[#4cd7f6] font-bold">¥ {selectedRecordForDetail.taxAmount.toLocaleString('zh-CN')} 元</p>
              </div>
              <div>
                <span className="text-[#8e909f]">凭证/发票号:</span>
                <p className="text-[#dae2fd]">{selectedRecordForDetail.invoiceCode || '暂无发票号'}</p>
              </div>
              <div>
                <span className="text-[#8e909f]">当前状态:</span>
                <p className="text-[#F59E0B] font-bold">{selectedRecordForDetail.status}</p>
              </div>
            </div>

            {/* 四流合一核查清单 */}
            <div>
              <h5 className="text-[13px] font-bold text-[#dae2fd] mb-2 flex items-center gap-1.5">
                <FileCheck2 className="w-4 h-4 text-[#4cd7f6]" />
                <span>四流合一链条交叉比对结论</span>
              </h5>
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <div className={`p-2 rounded border flex items-center justify-between ${
                  selectedRecordForDetail.fourFlowsCheck.contractMatch 
                    ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]' 
                    : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                }`}>
                  <span>合同签约主体一致性</span>
                  <span>{selectedRecordForDetail.fourFlowsCheck.contractMatch ? '符合' : '不一致'}</span>
                </div>
                <div className={`p-2 rounded border flex items-center justify-between ${
                  selectedRecordForDetail.fourFlowsCheck.invoiceMatch 
                    ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]' 
                    : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                }`}>
                  <span>发票开具品目与税率</span>
                  <span>{selectedRecordForDetail.fourFlowsCheck.invoiceMatch ? '符合' : '异常'}</span>
                </div>
                <div className={`p-2 rounded border flex items-center justify-between ${
                  selectedRecordForDetail.fourFlowsCheck.paymentMatch 
                    ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]' 
                    : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                }`}>
                  <span>银行公对公资金结算流</span>
                  <span>{selectedRecordForDetail.fourFlowsCheck.paymentMatch ? '符合' : '存疑'}</span>
                </div>
                <div className={`p-2 rounded border flex items-center justify-between ${
                  selectedRecordForDetail.fourFlowsCheck.logisticsMatch 
                    ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]' 
                    : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                }`}>
                  <span>实物仓储与运输过磅单</span>
                  <span>{selectedRecordForDetail.fourFlowsCheck.logisticsMatch ? '符合' : '缺失'}</span>
                </div>
              </div>
            </div>

            {/* 风险研判说明 */}
            {selectedRecordForDetail.riskDescription && (
              <div className="p-3 rounded-lg bg-[#222a3d] border border-[#444653]/40 text-[12px]">
                <p className="font-bold text-[#ffa583] mb-1">风险研判与审查意见：</p>
                <p className="text-[#dae2fd] leading-relaxed">{selectedRecordForDetail.riskDescription}</p>
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setSelectedRecordForDetail(null)}
                className="px-4 py-2 bg-[#2d3449] hover:bg-[#31394d] text-[#dae2fd] text-[13px] rounded-lg cursor-pointer"
              >
                关闭
              </button>
              <button
                onClick={() => {
                  const name = selectedRecordForDetail.entityName;
                  setSelectedRecordForDetail(null);
                  onAskAiAboutRisk(name);
                }}
                className="px-4 py-2 bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] text-[13px] font-semibold rounded-lg flex items-center gap-1.5 cursor-pointer"
              >
                <span>呼叫智能助手深度诊断</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
