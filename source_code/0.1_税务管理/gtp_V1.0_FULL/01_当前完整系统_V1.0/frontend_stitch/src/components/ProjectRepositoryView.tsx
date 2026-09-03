import { useEffect, useState } from 'react';
import {
  Building2,
  Search,
  Filter,
  Layers,
  MapPin,
  User,
  ChevronRight,
  LayoutGrid,
  Table as TableIcon,
  Download,
  BrainCircuit,
} from 'lucide-react';
import { DataStatus, MatchingCompletenessSummary, ProjectItem, SystemSettings } from '../types';
import { DataStatusCard } from './DataStatusCard';
import { fetchProjectMatchingCompleteness, summarizeMatchingCompleteness } from '../api';

interface ProjectRepositoryViewProps {
  projects: ProjectItem[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onRetry: () => void;
  onSelectProject: (projectId: string) => void;
  onOpenNewRecordModal: () => void;
  onOpenExportModal: () => void;
  onNavigateToAiReview?: (projectId: string) => void;
  settings?: SystemSettings;
}

export function ProjectRepositoryView({
  projects,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onSelectProject,
  onOpenExportModal,
  onNavigateToAiReview,
}: ProjectRepositoryViewProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRiskFilter, setSelectedRiskFilter] = useState('全部');
  const [selectedGradeFilter, setSelectedGradeFilter] = useState('全部');
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [flowCompleteness, setFlowCompleteness] = useState<MatchingCompletenessSummary & { loading: boolean }>({
    status: 'UNAVAILABLE',
    percentage: null,
    dataGaps: [],
    loading: true,
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const projectIds = [...new Set(
      projects
        .map(project => project.numericId)
        .filter(projectId => Number.isInteger(projectId) && projectId > 0),
    )];

    setFlowCompleteness(current => ({ ...current, loading: true }));
    if (projectIds.length === 0) {
      setFlowCompleteness({ status: 'UNAVAILABLE', percentage: null, dataGaps: [], loading: false });
      return () => {
        active = false;
        controller.abort();
      };
    }

    void Promise.allSettled(
      projectIds.map(projectId => fetchProjectMatchingCompleteness(projectId, controller.signal)),
    ).then(results => {
      if (!active || controller.signal.aborted) return;
      const responses = results
        .filter((result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof fetchProjectMatchingCompleteness>>> => result.status === 'fulfilled')
        .map(result => result.value);
      const summary = summarizeMatchingCompleteness(
        responses,
        results.filter(result => result.status === 'rejected').length,
      );
      setFlowCompleteness({ ...summary, loading: false });
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [projects]);

  const totalBudget = projects.reduce((acc, p) => acc + p.totalBudget, 0);
  const totalSpent = projects.reduce((acc, p) => acc + p.spentAmount, 0);
  const totalRemaining = totalBudget - totalSpent;
  const overallProgress = totalBudget > 0 ? ((totalSpent / totalBudget) * 100).toFixed(1) : '—';

  const formatPercentage = (value: number | null): string => {
    if (value === null) return '—';
    return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  };
  const flowStatusLabel = flowCompleteness.loading
    ? '正在加载'
    : flowCompleteness.status === 'AVAILABLE'
      ? '证据完整'
      : flowCompleteness.status === 'DEGRADED'
        ? '证据不完整'
        : '暂不可用';
  const flowPercentage = flowCompleteness.loading || flowCompleteness.status === 'UNAVAILABLE'
    ? '—'
    : formatPercentage(flowCompleteness.percentage);
  const flowStatusColor = flowCompleteness.loading || flowCompleteness.status === 'UNAVAILABLE'
    ? '#8e909f'
    : flowCompleteness.status === 'DEGRADED'
      ? '#F59E0B'
      : '#10B981';

  const filteredProjects = projects.filter(p => {
    const matchesSearch =
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.projectCode.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.managerName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.location.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesRisk =
      selectedRiskFilter === '全部' ||
      (selectedRiskFilter === '未知' && p.taxRiskGrade === '未知') ||
      (selectedRiskFilter === '中等偏高' && p.taxRiskGrade === '中等偏高') ||
      (selectedRiskFilter === '高危' && (p.taxRiskGrade === '高危' || p.isOverBudget));

    const matchesGrade =
      selectedGradeFilter === '全部' ||
      (selectedGradeFilter === '甲级' && p.healthGrade.startsWith('甲级')) ||
      (selectedGradeFilter === '乙级' && p.healthGrade.startsWith('乙级'));

    return matchesSearch && matchesRisk && matchesGrade;
  });

  const pageTitle = (
    <header data-page-title="projects" className="w-full">
      <div className="flex items-start gap-2.5">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border border-[#4cd7f6]/40 bg-gradient-to-br from-[#1e40af] to-[#03b5d3]/40 shadow-[0_0_12px_rgba(76,215,246,0.3)]">
          <Building2 className="h-5 w-5 text-[#4cd7f6]" />
        </div>
        <div>
          <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">项目工程库</h2>
          <p className="mt-1 text-[13px] text-[#c4c5d5]">仅展示税务接口已返回的项目及项目级经营摘要，主体与税务台账均从真实接口加载。</p>
        </div>
      </div>
    </header>
  );

  if (dataStatus !== 'READY' || projects.length === 0) {
    return (
      <div className="space-y-6">
        {pageTitle}
        <DataStatusCard status={dataStatus} title="项目工程库不可用" message={dataStatusMessage} onRetry={onRetry} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {pageTitle}

      <div data-page-controls="projects" className="glass-panel flex flex-wrap items-center justify-end gap-3 rounded-xl border border-[#444653]/30 p-3.5">
        <button
          onClick={onOpenExportModal}
          className="flex cursor-pointer items-center gap-2 rounded-lg border-t border-[#4cd7f6]/40 bg-[#1e40af] px-4 py-2 text-[13px] font-semibold text-[#dde1ff] shadow-md transition-colors hover:bg-[#1e40af]/80"
        >
          <Download className="h-4 w-4 text-[#4cd7f6]" />
          <span>导出工程库台账</span>
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="glass-panel glow-cyan flex flex-col justify-between rounded-xl p-4">
          <div className="flex items-center justify-between text-[12px] text-[#c4c5d5]"><span className="font-semibold">在建项目</span><span className="rounded border border-[#4cd7f6]/20 bg-[#03b5d3]/10 px-2 py-0.5 font-mono-num text-[#4cd7f6]">重点监管</span></div>
          <div className="my-2"><p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">{projects.length} <span className="text-[14px] font-normal text-[#8e909f]">个项目</span></p></div>
          <p className="text-[11px] text-[#8e909f]">按当前税务接口返回结果汇总</p>
        </div>

        <div className="glass-panel glow-blue flex flex-col justify-between rounded-xl p-4">
          <div className="flex items-center justify-between text-[12px] text-[#c4c5d5]"><span className="font-semibold">项目合同总额</span><span className="rounded border border-[#b8c4ff]/20 bg-[#1e40af]/20 px-2 py-0.5 font-mono-num text-[#b8c4ff]">接口口径</span></div>
          <div className="my-2"><p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">¥ {(totalBudget / 100000000).toFixed(2)} <span className="text-[14px] font-normal text-[#8e909f]">亿元</span></p></div>
          <p className="text-[11px] text-[#8e909f]">合同额减真实成本：¥ {(totalRemaining / 100000000).toFixed(2)} 亿元</p>
        </div>

        <div className="glass-panel glow-purple flex flex-col justify-between rounded-xl p-4">
          <div className="flex items-center justify-between text-[12px] text-[#c4c5d5]"><span className="font-semibold">真实成本</span><span className="rounded border border-[#10B981]/30 bg-[#10B981]/15 px-2 py-0.5 font-mono-num text-[#10B981]">消耗 {overallProgress === '—' ? '—' : `${overallProgress}%`}</span></div>
          <div className="my-2"><p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">¥ {(totalSpent / 100000000).toFixed(2)} <span className="text-[14px] font-normal text-[#8e909f]">亿元</span></p></div>
          <div className="h-1.5 w-full overflow-hidden rounded-full border border-[#444653]/30 bg-[#131b2e]"><div className="h-full bg-gradient-to-r from-[#10B981] to-[#4cd7f6]" style={{ width: overallProgress === '—' ? '0%' : `${overallProgress}%` }} /></div>
        </div>

        <div className="glass-panel glow-green flex flex-col justify-between rounded-xl p-4">
          <div className="flex items-center justify-between text-[12px] text-[#c4c5d5]"><span className="font-semibold">四流证据完整度</span><span className="rounded border px-2 py-0.5 font-mono-num" style={{ color: flowStatusColor, backgroundColor: `${flowStatusColor}26`, borderColor: `${flowStatusColor}4d` }}>{flowStatusLabel}</span></div>
          <div className="my-2"><p className="text-[26px] font-bold font-mono-num" style={{ color: flowStatusColor }}>{flowPercentage}<span className="text-[14px] font-normal text-[#8e909f]">{flowPercentage === '—' ? '' : '%'}</span></p></div>
          <p className="text-[11px] text-[#8e909f]">合同、履约、发票、付款证据由税务系统确定性匹配结果决定</p>
        </div>
      </div>

      <div className="glass-panel flex flex-wrap items-center justify-between gap-3 rounded-xl p-3.5">
        <div className="flex min-w-[280px] flex-1 flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#8e909f]" />
            <input type="text" placeholder="搜索项目名称、编号、负责人或地点..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} className="w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] py-1.5 pl-9 pr-3 text-[13px] font-mono-num text-[#dae2fd] placeholder-[#8e909f] transition-colors focus:border-[#4cd7f6]/60 focus:outline-none" />
          </div>

          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="flex items-center gap-1 text-[#8e909f]"><Filter className="h-3.5 w-3.5" /> 风险状态：</span>
            {['全部', '未知', '中等偏高', '高危'].map(rf => <button key={rf} onClick={() => setSelectedRiskFilter(rf)} className={`cursor-pointer rounded-md px-2.5 py-1 transition-all ${selectedRiskFilter === rf ? 'border border-[#4cd7f6]/40 bg-[#2d3449] font-bold text-[#4cd7f6]' : 'bg-[#131b2e] text-[#c4c5d5] hover:text-[#dae2fd]'}`}>{rf}</button>)}
          </div>

          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="text-[#8e909f]">评级：</span>
            {['全部', '甲级', '乙级'].map(gf => <button key={gf} onClick={() => setSelectedGradeFilter(gf)} className={`cursor-pointer rounded-md px-2.5 py-1 transition-all ${selectedGradeFilter === gf ? 'border border-[#4cd7f6]/40 bg-[#2d3449] font-bold text-[#4cd7f6]' : 'bg-[#131b2e] text-[#c4c5d5] hover:text-[#dae2fd]'}`}>{gf}</button>)}
          </div>
        </div>

        <div className="flex items-center gap-1 rounded-lg border border-[#444653]/40 bg-[#131b2e] p-1">
          <button onClick={() => setViewMode('grid')} className={`cursor-pointer rounded p-1.5 transition-colors ${viewMode === 'grid' ? 'bg-[#2d3449] text-[#4cd7f6]' : 'text-[#8e909f] hover:text-[#dae2fd]'}`} title="卡片矩阵视图"><LayoutGrid className="h-4 w-4" /></button>
          <button onClick={() => setViewMode('table')} className={`cursor-pointer rounded p-1.5 transition-colors ${viewMode === 'table' ? 'bg-[#2d3449] text-[#4cd7f6]' : 'text-[#8e909f] hover:text-[#dae2fd]'}`} title="表格列表视图"><TableIcon className="h-4 w-4" /></button>
        </div>
      </div>

      {viewMode === 'grid' ? (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {filteredProjects.map(proj => {
            const hasContractTotal = proj.totalBudget > 0;
            const isOverBudget = proj.isOverBudget ?? (hasContractTotal ? proj.spentAmount > proj.totalBudget : false);
            const progress = hasContractTotal ? ((proj.spentAmount / proj.totalBudget) * 100).toFixed(1) : '—';
            return (
              <div key={proj.id} className="glass-panel group flex flex-col justify-between overflow-hidden rounded-xl border border-[#444653]/40 transition-all duration-200 hover:border-[#4cd7f6]/60 hover:shadow-[0_0_20px_rgba(76,215,246,0.15)]">
                <div className="border-b border-[#444653]/30 bg-[#171f33]/70 p-4">
                  <div className="flex items-start justify-between gap-3"><div className="min-w-0 flex-1"><div className="mb-1.5 flex flex-wrap items-center gap-2"><span className="rounded border border-[#444653]/60 bg-[#131b2e] px-2 py-0.5 text-[11px] font-semibold font-mono-num text-[#4cd7f6]">{proj.projectCode}</span><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${proj.healthGrade.startsWith('甲级') ? 'border-[#10B981]/30 bg-[#10B981]/15 text-[#10B981]' : 'border-[#F59E0B]/30 bg-[#F59E0B]/15 text-[#F59E0B]'}`}>{proj.healthGrade}</span></div><h3 className="line-clamp-2 text-[16px] font-bold leading-snug text-[#dae2fd] transition-colors group-hover:text-[#4cd7f6]">{proj.name}</h3></div></div>
                  <div className="mt-2.5 flex items-center gap-1.5 text-[12px] text-[#c4c5d5]"><Layers className="h-3.5 w-3.5 flex-shrink-0 text-[#4cd7f6]" /><span className="truncate">{proj.constructionStage}</span></div>
                </div>

                <div className="flex-1 space-y-3.5 p-4">
                  <div className="grid grid-cols-3 gap-2 rounded-lg border border-[#444653]/20 bg-[#131b2e]/60 p-2.5 text-center">
                    <div><p className="text-[10px] text-[#8e909f]">项目合同总额</p><p className="mt-0.5 text-[13px] font-bold font-mono-num text-[#dae2fd]">¥ {(proj.totalBudget / 100000000).toFixed(2)}亿</p></div>
                    <div><p className="text-[10px] text-[#8e909f]">真实成本</p><p className="mt-0.5 text-[13px] font-bold font-mono-num text-[#ffa583]">¥ {(proj.spentAmount / 100000000).toFixed(2)}亿</p></div>
                    <div><p className="text-[10px] text-[#8e909f]">剩余可用</p><p className="mt-0.5 text-[13px] font-bold font-mono-num text-[#10B981]">¥ {(proj.remainingBudget / 100000000).toFixed(2)}亿</p></div>
                  </div>

                  <div><div className="mb-1 flex justify-between text-[11px] font-mono-num"><span className="text-[#8e909f]">预算执行进度</span><span className={`font-bold ${isOverBudget ? 'text-[#EF4444]' : 'text-[#4cd7f6]'}`}>{progress === '—' ? '—' : `${progress}%`} {isOverBudget ? '（超合同额）' : ''}</span></div><div className="h-2 w-full overflow-hidden rounded-full border border-[#444653]/30 bg-[#131b2e]"><div className={`h-full rounded-full transition-all duration-500 ${isOverBudget ? 'bg-[#EF4444]' : 'bg-gradient-to-r from-[#1e40af] via-[#03b5d3] to-[#4cd7f6]'}`} style={{ width: progress === '—' ? '0%' : `${Math.min(parseFloat(progress), 100)}%` }} /></div></div>

                  <div className="flex items-center justify-between border-t border-[#444653]/20 pt-1 text-[11px] text-[#8e909f]"><div className="flex items-center gap-1.5"><span>税务风险：</span><strong className={`font-mono-num ${proj.taxRiskGrade === '高危' ? 'text-[#EF4444]' : proj.taxRiskGrade === '中等偏高' ? 'text-[#F59E0B]' : 'text-[#10B981]'}`}>{proj.taxRiskGrade}</strong></div><span>{proj.constructionStage}</span></div>
                  <div className="space-y-1 text-[11px] text-[#8e909f]"><div className="flex items-center gap-1.5 truncate"><User className="h-3 w-3 flex-shrink-0 text-[#4cd7f6]" /><span className="truncate">{proj.managerName}</span></div><div className="flex items-center gap-1.5 truncate"><MapPin className="h-3 w-3 flex-shrink-0 text-[#8e909f]" /><span className="truncate">{proj.location}</span></div></div>
                </div>

                <div className="flex items-center justify-between gap-2 border-t border-[#444653]/30 bg-[#0e172a]/60 p-3.5">
                  <button onClick={() => onSelectProject(proj.id)} className="flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-[#4cd7f6]/30 bg-[#1e40af]/60 px-3 py-1.5 text-[12px] font-semibold text-[#dde1ff] shadow-sm transition-all hover:border-[#4cd7f6] hover:bg-[#1e40af]"><span>穿透业财详情</span><ChevronRight className="h-3.5 w-3.5 text-[#4cd7f6]" /></button>
                  {onNavigateToAiReview && <button onClick={() => onNavigateToAiReview(proj.id)} className="cursor-pointer rounded-lg border border-[#444653]/40 bg-[#131b2e] p-2 text-[#4cd7f6] transition-colors hover:bg-[#222a3d] hover:text-white" title="智能专项审查"><BrainCircuit className="h-4 w-4" /></button>}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="glass-panel overflow-hidden rounded-xl border border-[#444653]/40">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="select-none border-b border-[#444653]/30 bg-[#171f33]/80 font-semibold text-[#8e909f]"><tr><th className="p-3.5 pl-4">工程编号</th><th className="p-3.5">项目名称</th><th className="p-3.5">当前施工阶段</th><th className="p-3.5 text-right">合同总额</th><th className="p-3.5 text-right">累计支出</th><th className="p-3.5 text-center">资金进度</th><th className="p-3.5 text-center">健康评级</th><th className="p-3.5 text-center">税务风险</th><th className="p-3.5 pr-4 text-center">操作</th></tr></thead>
              <tbody className="divide-y divide-[#444653]/20">
                {filteredProjects.map(proj => {
                  const progress = proj.totalBudget > 0 ? ((proj.spentAmount / proj.totalBudget) * 100).toFixed(1) : '—';
                  return <tr key={proj.id} className="group transition-colors hover:bg-[#1e40af]/10"><td className="p-3.5 pl-4 font-semibold font-mono-num text-[#4cd7f6]">{proj.projectCode}</td><td className="max-w-[240px] truncate p-3.5 font-bold text-[#dae2fd]">{proj.name}</td><td className="max-w-[200px] truncate p-3.5 text-[#c4c5d5]">{proj.constructionStage}</td><td className="p-3.5 text-right font-mono-num text-[#dae2fd]">¥ {(proj.totalBudget / 100000000).toFixed(2)} 亿</td><td className="p-3.5 text-right font-mono-num text-[#ffa583]">¥ {(proj.spentAmount / 100000000).toFixed(2)} 亿</td><td className="p-3.5 text-center font-bold font-mono-num text-[#4cd7f6]">{progress === '—' ? '—' : `${progress}%`}</td><td className="p-3.5 text-center"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${proj.healthGrade.startsWith('甲级') ? 'border-[#10B981]/30 bg-[#10B981]/15 text-[#10B981]' : 'border-[#F59E0B]/30 bg-[#F59E0B]/15 text-[#F59E0B]'}`}>{proj.healthGrade}</span></td><td className="p-3.5 text-center font-mono-num text-[#dae2fd]"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${proj.taxRiskGrade === '高危' ? 'border-[#EF4444]/30 bg-[#EF4444]/15 text-[#EF4444]' : 'border-[#10B981]/30 bg-[#10B981]/15 text-[#10B981]'}`}>{proj.taxRiskGrade}</span></td><td className="p-3.5 pr-4 text-center"><button onClick={() => onSelectProject(proj.id)} className="cursor-pointer font-semibold text-[#4cd7f6] hover:text-[#dae2fd] hover:underline">穿透详情</button></td></tr>;
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
