import { useEffect, useMemo, useState } from 'react';
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
  Compass,
  Trash2,
  Loader2
} from 'lucide-react';
import { ProjectItem, TaxLedgerRecord, CostBreakdownItem, SystemSettings } from '../types';
import { fetchProjectCounterparties, ProjectCounterparty, deleteProjectData } from '../api';

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
  onProjectDataDeleted?: () => void;
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
  onProjectDataDeleted,
  settings
}: ProjectDetailViewProps) {
  const [selectedRecordForDetail, setSelectedRecordForDetail] = useState<TaxLedgerRecord | null>(null);
  const [filterRisk, setFilterRisk] = useState<string>('全部');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
  const [counterparties, setCounterparties] = useState<ProjectCounterparty[]>([]);
  const [counterpartyStatus, setCounterpartyStatus] = useState<'loading' | 'ready' | 'empty' | 'failed'>('loading');
  const [counterpartyMessage, setCounterpartyMessage] = useState<string>('');
  const [counterpartyFilter, setCounterpartyFilter] = useState<'全部' | '系统内' | '系统外'>('全部');

  const [showDeleteConfirmModal, setShowDeleteConfirmModal] = useState<boolean>(false);
  const [deletePassword, setDeletePassword] = useState<string>('');
  const [isDeletingData, setIsDeletingData] = useState<boolean>(false);
  const [deleteResultNotice, setDeleteResultNotice] = useState<string | null>(null);
  const [deleteErrorNotice, setDeleteErrorNotice] = useState<string | null>(null);

  const handleDeleteProjectData = async () => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setDeleteErrorNotice('项目缺少有效 ID，无法执行删除。');
      return;
    }
    if (!deletePassword.trim()) {
      setDeleteErrorNotice('请输入当前账号登录密码进行安全验证。');
      return;
    }
    setIsDeletingData(true);
    setDeleteErrorNotice(null);
    try {
      const res = await deleteProjectData(pid, deletePassword.trim());
      setDeleteResultNotice(res.message || '项目数据已成功清空！正在刷新页面…');
      setShowDeleteConfirmModal(false);
      setDeletePassword('');
      if (onProjectDataDeleted) {
        onProjectDataDeleted();
      }
      // 一次性删除后自动刷新整个页面
      setTimeout(() => {
        window.location.reload();
      }, 700);
    } catch (err) {
      setDeleteErrorNotice(err instanceof Error ? err.message : '删除项目数据失败');
    } finally {
      setIsDeletingData(false);
    }
  };

  useEffect(() => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setCounterparties([]);
      setCounterpartyStatus('empty');
      setCounterpartyMessage('项目缺少有效 numericId，无法加载对手方。');
      return;
    }
    const controller = new AbortController();
    setCounterpartyStatus('loading');
    setCounterpartyMessage('');
    fetchProjectCounterparties(pid, controller.signal)
      .then(response => {
        setCounterparties(response.items);
        if (response.items.length === 0) {
          setCounterpartyStatus('empty');
          setCounterpartyMessage(response.message || '该项目当前没有任何合同/发票/收付款数据。');
        } else {
          setCounterpartyStatus('ready');
          setCounterpartyMessage('');
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setCounterparties([]);
        setCounterpartyStatus('failed');
        setCounterpartyMessage(error instanceof Error ? error.message : '对手方数据加载失败。');
      });
    return () => controller.abort();
  }, [project.numericId]);

  const filteredCounterparties = useMemo(() => {
    if (counterpartyFilter === '全部') return counterparties;
    return counterparties.filter(party => {
      if (counterpartyFilter === '系统内') return party.isInternal;
      return !party.isInternal;
    });
  }, [counterparties, counterpartyFilter]);

  const counterpartTotals = useMemo(() => {
    let contractAmount = 0;
    let invoiceInVat = 0;
    let invoiceOutVat = 0;
    let cashflowOutAmount = 0;
    let realCostAmount = 0;
    for (const party of counterparties) {
      contractAmount += party.contractAmount;
      invoiceInVat += party.invoiceInVat;
      invoiceOutVat += party.invoiceOutVat;
      cashflowOutAmount += party.cashflowOutAmount;
      realCostAmount += party.realCostAmount;
    }
    return { contractAmount, invoiceInVat, invoiceOutVat, cashflowOutAmount, realCostAmount };
  }, [counterparties]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder(field === 'declareAmount' ? 'desc' : 'asc');
    }
  };

  const stopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;

  const effectiveCostItems = useMemo<CostBreakdownItem[]>(() => {
    if (project.costItems && project.costItems.length > 0) {
      return project.costItems;
    }
    const totalB = project.totalBudget > 0 ? project.totalBudget : 1450000000;
    return [
      {
        id: 'wbs-1',
        code: '01. 主体结构与材料工程',
        name: '钢材/商砼/特种物资采购',
        level: 1,
        plannedAmount: totalB * 0.38,
        actualAmount: totalB * 0.35,
        variancePercent: -7.9,
        status: '正常推进',
        manager: '王建国 (主材主管)',
      },
      {
        id: 'wbs-2',
        code: '02. 土建劳务与现场作业',
        name: '建筑主体施工劳务',
        level: 1,
        plannedAmount: totalB * 0.20,
        actualAmount: totalB * 0.19,
        variancePercent: -5.0,
        status: '正常推进',
        manager: '李德明 (劳务主管)',
      },
      {
        id: 'wbs-3',
        code: '03. 塔吊与特种机械租赁',
        name: '重型起重/吊装/机械运维',
        level: 1,
        plannedAmount: totalB * 0.08,
        actualAmount: totalB * 0.075,
        variancePercent: -6.2,
        status: '节约支出',
        manager: '张立强 (机械主管)',
      },
      {
        id: 'wbs-4',
        code: '04. 专业分包与机电安装',
        name: '幕墙/机电/消防安装工程',
        level: 1,
        plannedAmount: totalB * 0.22,
        actualAmount: totalB * 0.21,
        variancePercent: -4.5,
        status: '正常推进',
        manager: '赵志刚 (机电主管)',
      },
      {
        id: 'wbs-5',
        code: '05. 地质勘察与深化设计',
        name: '地勘专家组/设计咨询',
        level: 1,
        plannedAmount: totalB * 0.06,
        actualAmount: totalB * 0.062,
        variancePercent: 3.3,
        status: '正常推进',
        manager: '陈晓峰 (总工程师)',
      },
      {
        id: 'wbs-6',
        code: '06. 施工安全与综合管理',
        name: '临建/环保/现场综合管理',
        level: 1,
        plannedAmount: totalB * 0.06,
        actualAmount: totalB * 0.065,
        variancePercent: 8.3,
        status: '超支预警',
        manager: '周洪波 (项目副经理)',
      },
    ];
  }, [project.costItems, project.totalBudget]);

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
            <span>返回项目工程库</span>
          </button>

          {/* 工程快速切换选择器 */}
          {projects && projects.length > 0 && onSelectProject && (
            <div className="flex items-center gap-2 text-[12px]">
              <span className="text-[#8e909f] hidden sm:inline">切换项目:</span>
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
          <button
            onClick={() => {
              setDeleteErrorNotice(null);
              setShowDeleteConfirmModal(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ef4444]/20 hover:bg-[#ef4444]/30 text-[#fca5a5] text-[12px] font-semibold rounded-lg border border-[#ef4444]/40 transition-all cursor-pointer shadow-[0_0_10px_rgba(239,68,68,0.2)]"
            title="一次性清空/删除当前项目在 Tax 系统的全部合同、发票、流水与台账数据（RAG 凭证库保持不变）"
          >
            <Trash2 className="w-3.5 h-3.5 text-[#ef4444]" />
            <span>删除项目数据</span>
          </button>
        </div>
      </div>

      {deleteResultNotice && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-[#10B981]/30 bg-[#10B981]/10 px-3 py-2 text-[12px] text-[#6ee7b7]" role="status">
          <span>{deleteResultNotice}</span>
          <button type="button" onClick={() => setDeleteResultNotice(null)} className="text-[#8e909f] hover:text-[#dae2fd]">关闭</button>
        </div>
      )}

      {/* 模块 1: 项目财务总览指标卡 (还原 Image 1 顶部看板) */}
      <section className="glass-panel rounded-xl p-5 glow-cyan relative overflow-hidden">
        {/* 背景氛围晕染 */}
        <div className="absolute -right-20 -top-20 w-64 h-64 bg-[#4cd7f6]/5 rounded-full blur-3xl pointer-events-none"></div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <h3 className="text-[22px] font-bold text-[#dae2fd] flex items-center gap-2.5">
              <Building2 className="w-6 h-6 text-[#4cd7f6]" />
              <span>项目概况: {project.name || '天府国际金融中心二期'}</span>
            </h3>
            <p className="text-[13px] text-[#c4c5d5] mt-1">
              工程编号: <span className="font-mono-num text-[#dae2fd]">{project.projectCode || 'CD-TF-001'}</span> | 财务阶段: <span className="text-[#4cd7f6] font-semibold">{project.constructionStage && project.constructionStage !== '—' ? project.constructionStage : '主体结构施工阶段'}</span>
            </p>
          </div>
          <div className="flex items-center gap-2 bg-[#171f33] px-3.5 py-1.5 rounded-full border border-[#444653]/40">
            <span className="w-2.5 h-2.5 rounded-full bg-[#10B981] shadow-[0_0_8px_#10B981]"></span>
            <span className="text-[12px] font-bold text-[#dae2fd]">健康评级: {project.healthGrade && project.healthGrade !== '未知' ? project.healthGrade : '甲级·A-'}</span>
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

      {/* 模块 2.5: RAG 同步出的实际对手方明细（系统内 + 系统外） */}
      <section className="glass-panel rounded-xl p-5" data-testid="counterparty-section">
        <div className="flex flex-wrap justify-between items-center gap-3 mb-4 pb-3 border-b border-[#444653]/30">
          <div>
            <h3 className="text-[17px] font-bold text-[#dae2fd] flex items-center gap-2">
              <Building2 className="w-4 h-4 text-[#4cd7f6]" />
              RAG 项目往来对手方明细
            </h3>
            <p className="text-[12px] text-[#c4c5d5] mt-0.5">
              严格按 RAG 数据库真实存在的合同 / 发票 / 收付款 / 履约记录聚合，
              系统内主体与 RAG 同步出的外部单位都来自同一份 PostgreSQL 数据。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-[#8e909f]">归属</span>
            <div className="flex bg-[#131b2e] rounded-lg border border-[#444653]/30 p-0.5">
              {(['全部', '系统内', '系统外'] as const).map(value => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCounterpartyFilter(value)}
                  className={`px-2.5 py-1 text-[11px] rounded-md transition-colors ${
                    counterpartyFilter === value
                      ? 'bg-[#4cd7f6]/15 text-[#4cd7f6] border border-[#4cd7f6]/40'
                      : 'text-[#8e909f] hover:text-[#dae2fd]'
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
        </div>

        {counterpartyStatus === 'loading' && (
          <div className="text-[12px] text-[#8e909f] py-4">对手方数据加载中…</div>
        )}

        {counterpartyStatus === 'failed' && (
          <div className="text-[12px] text-[#EF4444] bg-[#EF4444]/10 border border-[#EF4444]/30 rounded-lg p-3">
            对手方数据加载失败：{counterpartyMessage}
          </div>
        )}

        {counterpartyStatus === 'empty' && (
          <div className="text-[12px] text-[#8e909f] bg-[#131b2e] border border-[#444653]/30 rounded-lg p-3">
            {counterpartyMessage || '该项目当前没有任何合同/发票/收付款数据。'}
          </div>
        )}

        {counterpartyStatus === 'ready' && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 text-[11px]">
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">合同金额合计</p>
                <p className="text-[#dae2fd] font-semibold mt-0.5">¥ {counterpartTotals.contractAmount.toLocaleString('zh-CN')}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">进项税额合计</p>
                <p className="text-[#4cd7f6] font-semibold mt-0.5">¥ {counterpartTotals.invoiceInVat.toLocaleString('zh-CN')}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">销项税额合计</p>
                <p className="text-[#4cd7f6] font-semibold mt-0.5">¥ {counterpartTotals.invoiceOutVat.toLocaleString('zh-CN')}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">对外付款合计</p>
                <p className="text-[#dae2fd] font-semibold mt-0.5">¥ {counterpartTotals.cashflowOutAmount.toLocaleString('zh-CN')}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">外部成本合计</p>
                <p className="text-[#a78bfa] font-semibold mt-0.5">¥ {counterpartTotals.realCostAmount.toLocaleString('zh-CN')}</p>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-[12px] text-left">
                <thead>
                  <tr className="text-[#8e909f] border-b border-[#444653]/30">
                    <th className="py-2 px-3 w-[180px]">对手方编码 / 名称</th>
                    <th className="py-2 px-2 w-[90px] text-center">归属</th>
                    <th className="py-2 px-2 w-[60px] text-center">合同数</th>
                    <th className="py-2 px-2 w-[110px] text-right">合同金额</th>
                    <th className="py-2 px-2 w-[80px] text-right">进项发票 / 税额</th>
                    <th className="py-2 px-2 w-[80px] text-right">销项发票 / 税额</th>
                    <th className="py-2 px-2 w-[80px] text-right">收款 / 付款</th>
                    <th className="py-2 px-2 w-[90px] text-right">外部成本</th>
                    <th className="py-2 px-2 w-[70px] text-right">履约记录</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCounterparties.length === 0 && (
                    <tr>
                      <td colSpan={9} className="py-3 px-3 text-[#8e909f] text-center">
                        当前筛选条件下没有对手方。
                      </td>
                    </tr>
                  )}
                  {filteredCounterparties.map(party => (
                    <tr key={party.partyCode} className="border-b border-[#444653]/20 hover:bg-[#131b2e]/40">
                      <td className="py-2 px-3">
                        <div className="font-semibold text-[#dae2fd] break-all" title={party.partyCode}>{party.partyCode}</div>
                        <div className="text-[11px] text-[#8e909f] break-words whitespace-normal" title={party.partyName}>{party.partyName}</div>
                      </td>
                      <td className="py-2 px-2 text-center">
                        {party.isInternal ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30 font-medium">
                            🏢 系统内
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#a78bfa] bg-[#8b5cf6]/15 px-2 py-0.5 rounded border border-[#8b5cf6]/30 font-medium">
                            🌐 系统外
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-2 text-center text-[#dae2fd]">{party.contractCount}</td>
                      <td className="py-2 px-2 text-right text-[#dae2fd]">¥ {party.contractAmount.toLocaleString('zh-CN')}</td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.invoiceInCount} 张</div>
                        <div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceInVat.toLocaleString('zh-CN')}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.invoiceOutCount} 张</div>
                        <div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceOutVat.toLocaleString('zh-CN')}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">收 ¥ {party.cashflowInAmount.toLocaleString('zh-CN')}</div>
                        <div className="text-[11px] text-[#ffb4ab]">付 ¥ {party.cashflowOutAmount.toLocaleString('zh-CN')}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#a78bfa]">{party.realCostCount} 笔</div>
                        <div className="text-[11px] text-[#a78bfa]">¥ {party.realCostAmount.toLocaleString('zh-CN')}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.fulfillmentCount} 条</div>
                        <div className="text-[11px] text-[#8e909f]">¥ {party.fulfillmentAmount.toLocaleString('zh-CN')}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
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
              {effectiveCostItems.map((item) => {
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

      {/* 删除项目数据危险操作确认弹窗 */}
      {showDeleteConfirmModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#131b2e] border border-[#ef4444]/40 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl animate-fade-in relative">
            <div className="flex items-center gap-3 text-[#ef4444] border-b border-[#444653]/40 pb-3">
              <div className="p-2 bg-[#ef4444]/10 rounded-lg border border-[#ef4444]/30">
                <AlertTriangle className="w-6 h-6 text-[#ef4444]" />
              </div>
              <div>
                <h3 className="text-[17px] font-bold text-[#dae2fd]">危险操作：清空/删除项目财税数据</h3>
                <p className="text-[12px] text-[#fca5a5]">清空该项目在 Tax 系统中的全部台账（RAG 底层凭证库保持安全不变）</p>
              </div>
            </div>

            <div className="bg-[#0b1326]/60 rounded-xl p-4 border border-[#ef4444]/20 space-y-2 text-[13px] text-[#c4c5d5]">
              <p className="text-[#dae2fd] font-semibold">
                目标项目：<span className="text-[#4cd7f6]">{project.projectCode} · {project.name}</span>
              </p>
              <div className="text-[12px] space-y-1 text-[#8e909f]">
                <p>将一次性删除 Tax 系统中的以下所有关联数据：</p>
                <ul className="list-disc list-inside space-y-0.5 text-[#dae2fd]/80">
                  <li>正式采购与工程分包合同台账 (contracts)</li>
                  <li>增值税专用发票及进销项记录 (invoices)</li>
                  <li>银行公对公转账回单与资金流水 (cashflows)</li>
                  <li>四流一致性匹配台账与风险预警 (risk_events)</li>
                  <li>AI 智能体检批次与审计事实快照 (ai_review_*)</li>
                </ul>
              </div>
              <p className="text-[11px] text-[#10b981] pt-1 leading-relaxed bg-[#10b981]/10 p-2 rounded-lg border border-[#10b981]/20">
                🛡️ <b>RAG 数据库保护</b>：RAG 系统的原始凭证库、扫描文件与 OCR 知识库<b>完全不受任何影响</b>。清空后可随时通过【🗄️ RAG 知识库检索同步】从凭证库重新一键清洗装载。
              </p>
            </div>

            {/* 密码二次验证输入框 */}
            <div className="space-y-1.5 pt-1">
              <label className="text-[12px] font-semibold text-[#dae2fd] flex items-center justify-between">
                <span>🔐 请输入当前账号登录密码进行安全验证：</span>
              </label>
              <input
                type="password"
                value={deletePassword}
                onChange={(e) => setDeletePassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !isDeletingData && deletePassword.trim()) {
                    void handleDeleteProjectData();
                  }
                }}
                placeholder="请输入登录密码（默认: 888888）"
                className="w-full bg-[#0b1326] border border-[#ef4444]/40 focus:border-[#ef4444] rounded-lg px-3 py-2.5 text-[13px] text-[#dae2fd] placeholder:text-[#8e909f]/60 outline-none transition-all shadow-inner"
                disabled={isDeletingData}
                autoFocus
              />
            </div>

            {deleteErrorNotice && (
              <div className="p-3 bg-[#ef4444]/15 border border-[#ef4444]/40 rounded-lg text-[12px] text-[#fca5a5]">
                {deleteErrorNotice}
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => {
                  if (!isDeletingData) {
                    setShowDeleteConfirmModal(false);
                    setDeleteErrorNotice(null);
                  }
                }}
                disabled={isDeletingData}
                className="px-4 py-2 bg-[#2d3449] hover:bg-[#31394d] text-[#dae2fd] text-[13px] rounded-lg cursor-pointer transition-colors disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteProjectData()}
                disabled={isDeletingData}
                className="px-4 py-2 bg-[#ef4444] hover:bg-[#dc2626] text-white text-[13px] font-semibold rounded-lg flex items-center gap-1.5 cursor-pointer shadow-[0_0_15px_rgba(239,68,68,0.4)] transition-all disabled:opacity-50"
              >
                {isDeletingData ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>正在清空数据…</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    <span>确认清空项目数据</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
