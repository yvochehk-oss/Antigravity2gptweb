import { useState } from 'react';
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
import { ProjectItem, SystemSettings } from '../types';

interface ProjectRepositoryViewProps {
  projects: ProjectItem[];
  onSelectProject: (projectId: string) => void;
  onOpenNewRecordModal: () => void;
  onOpenExportModal: () => void;
  onNavigateToAiReview?: (projectId: string) => void;
  settings?: SystemSettings;
}

export function ProjectRepositoryView({
  projects,
  onSelectProject,
  onOpenNewRecordModal,
  onOpenExportModal,
  onNavigateToAiReview,
  settings
}: ProjectRepositoryViewProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRiskFilter, setSelectedRiskFilter] = useState('全部');
  const [selectedGradeFilter, setSelectedGradeFilter] = useState('全部');
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');

  // 计算工程库综合统计
  const totalBudget = projects.reduce((acc, p) => acc + p.totalBudget, 0);
  const totalSpent = projects.reduce((acc, p) => acc + p.spentAmount, 0);
  const totalRemaining = totalBudget - totalSpent;
  const overallProgress = ((totalSpent / totalBudget) * 100).toFixed(1);

  // 过滤工程
  const filteredProjects = projects.filter(p => {
    const matchesSearch = 
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.projectCode.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.managerName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.location.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesRisk = 
      selectedRiskFilter === '全部' || 
      (selectedRiskFilter === '正常' && p.taxRiskGrade === '正常') ||
      (selectedRiskFilter === '中等偏高' && p.taxRiskGrade === '中等偏高') ||
      (selectedRiskFilter === '高危' && (p.taxRiskGrade === '高危' || p.isOverBudget));

    const matchesGrade = 
      selectedGradeFilter === '全部' ||
      (selectedGradeFilter === '甲级' && p.healthGrade.startsWith('甲级')) ||
      (selectedGradeFilter === '乙级' && p.healthGrade.startsWith('乙级'));

    return matchesSearch && matchesRisk && matchesGrade;
  });

  return (
    <div className="space-y-6">
      {/* 顶部标题与快速动作栏 */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#1e40af] to-[#03b5d3]/40 flex items-center justify-center border border-[#4cd7f6]/40 shadow-[0_0_12px_rgba(76,215,246,0.3)]">
              <Building2 className="w-5 h-5 text-[#4cd7f6]" />
            </div>
            <div>
              <h2 className="text-[26px] font-bold text-[#dae2fd] tracking-tight">标杆项目工程库全景</h2>
              <p className="text-[13px] text-[#c4c5d5] mt-0.5">
                全量穿透监管 26 家系统内独立法人关联企业承建的 5 大标杆示范工程业财履约全景。
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onOpenExportModal}
            className="bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] px-4 py-2 rounded-lg text-[13px] font-semibold border-t border-[#4cd7f6]/40 transition-colors flex items-center gap-2 cursor-pointer shadow-md"
          >
            <Download className="w-4 h-4 text-[#4cd7f6]" />
            <span>导出工程库台账</span>
          </button>
        </div>
      </div>

      {/* 4 大工程库综合指标汇总 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 指标 1: 工程总量 */}
        <div className="glass-panel rounded-xl p-4 glow-cyan flex flex-col justify-between">
          <div className="flex justify-between items-center text-[12px] text-[#c4c5d5]">
            <span className="font-semibold">在建标杆工程</span>
            <span className="font-mono-num text-[#4cd7f6] bg-[#03b5d3]/10 px-2 py-0.5 rounded border border-[#4cd7f6]/20">重点监管</span>
          </div>
          <div className="my-2">
            <p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">
              {projects.length} <span className="text-[14px] font-normal text-[#8e909f]">个示范标段</span>
            </p>
          </div>
          <p className="text-[11px] text-[#8e909f] flex items-center gap-1">
            <span>覆盖跨省及川内重点基建经济圈</span>
          </p>
        </div>

        {/* 指标 2: 总批复预算概算 */}
        <div className="glass-panel rounded-xl p-4 glow-blue flex flex-col justify-between">
          <div className="flex justify-between items-center text-[12px] text-[#c4c5d5]">
            <span className="font-semibold">总批复预算概算</span>
            <span className="font-mono-num text-[#b8c4ff] bg-[#1e40af]/20 px-2 py-0.5 rounded border border-[#b8c4ff]/20">概算总控</span>
          </div>
          <div className="my-2">
            <p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">
              ¥ {(totalBudget / 100000000).toFixed(2)} <span className="text-[14px] font-normal text-[#8e909f]">亿元</span>
            </p>
          </div>
          <p className="text-[11px] text-[#8e909f]">
            <span>剩余可用概算：¥ {(totalRemaining / 100000000).toFixed(2)} 亿元</span>
          </p>
        </div>

        {/* 指标 3: 累计发生支出与资金消耗 */}
        <div className="glass-panel rounded-xl p-4 glow-purple flex flex-col justify-between">
          <div className="flex justify-between items-center text-[12px] text-[#c4c5d5]">
            <span className="font-semibold">累计发生支出 (已付)</span>
            <span className="font-mono-num text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30">消耗 {overallProgress}%</span>
          </div>
          <div className="my-2">
            <p className="text-[26px] font-bold font-mono-num text-[#dae2fd]">
              ¥ {(totalSpent / 100000000).toFixed(2)} <span className="text-[14px] font-normal text-[#8e909f]">亿元</span>
            </p>
          </div>
          <div className="w-full bg-[#131b2e] rounded-full h-1.5 overflow-hidden border border-[#444653]/30">
            <div className="bg-gradient-to-r from-[#10B981] to-[#4cd7f6] h-full" style={{ width: `${overallProgress}%` }}></div>
          </div>
        </div>

        {/* 指标 4: 业财合规评级 */}
        <div className="glass-panel rounded-xl p-4 glow-green flex flex-col justify-between">
          <div className="flex justify-between items-center text-[12px] text-[#c4c5d5]">
            <span className="font-semibold">履约健康度</span>
            <span className="font-mono-num text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30">综合甲级</span>
          </div>
          <div className="my-2">
            <p className="text-[26px] font-bold font-mono-num text-[#10B981]">
              92.5 <span className="text-[14px] font-normal text-[#8e909f]">分 · 优良</span>
            </p>
          </div>
          <p className="text-[11px] text-[#8e909f]">
            <span>关联 26 家法人主体四流合一核查</span>
          </p>
        </div>
      </div>

      {/* 搜索与过滤工具栏 */}
      <div className="glass-panel rounded-xl p-3.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3 flex-1 min-w-[280px]">
          {/* 搜索框 */}
          <div className="relative flex-1 min-w-[220px]">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f]" />
            <input
              type="text"
              placeholder="搜索工程名称、编号 (如 CD-TF...)、负责人或地点..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 bg-[#131b2e] border border-[#444653]/40 rounded-lg text-[13px] text-[#dae2fd] placeholder-[#8e909f] focus:outline-none focus:border-[#4cd7f6]/60 transition-colors font-mono-num"
            />
          </div>

          {/* 风险筛选 */}
          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="text-[#8e909f] flex items-center gap-1">
              <Filter className="w-3.5 h-3.5" /> 风险状态:
            </span>
            {['全部', '正常', '中等偏高', '高危'].map(rf => (
              <button
                key={rf}
                onClick={() => setSelectedRiskFilter(rf)}
                className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                  selectedRiskFilter === rf
                    ? 'bg-[#2d3449] text-[#4cd7f6] font-bold border border-[#4cd7f6]/40'
                    : 'text-[#c4c5d5] hover:text-[#dae2fd] bg-[#131b2e]'
                }`}
              >
                {rf}
              </button>
            ))}
          </div>

          {/* 评级筛选 */}
          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="text-[#8e909f]">评级:</span>
            {['全部', '甲级', '乙级'].map(gf => (
              <button
                key={gf}
                onClick={() => setSelectedGradeFilter(gf)}
                className={`px-2.5 py-1 rounded-md transition-all cursor-pointer ${
                  selectedGradeFilter === gf
                    ? 'bg-[#2d3449] text-[#4cd7f6] font-bold border border-[#4cd7f6]/40'
                    : 'text-[#c4c5d5] hover:text-[#dae2fd] bg-[#131b2e]'
                }`}
              >
                {gf}
              </button>
            ))}
          </div>
        </div>

        {/* 视图切换按钮 */}
        <div className="flex items-center gap-1 bg-[#131b2e] p-1 rounded-lg border border-[#444653]/40">
          <button
            onClick={() => setViewMode('grid')}
            className={`p-1.5 rounded cursor-pointer transition-colors ${
              viewMode === 'grid' ? 'bg-[#2d3449] text-[#4cd7f6]' : 'text-[#8e909f] hover:text-[#dae2fd]'
            }`}
            title="卡片矩阵视图"
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
          <button
            onClick={() => setViewMode('table')}
            className={`p-1.5 rounded cursor-pointer transition-colors ${
              viewMode === 'table' ? 'bg-[#2d3449] text-[#4cd7f6]' : 'text-[#8e909f] hover:text-[#dae2fd]'
            }`}
            title="表格列表视图"
          >
            <TableIcon className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 5 大工程展示区域 */}
      {viewMode === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {filteredProjects.map(proj => {
            const isOverBudget = proj.spentAmount > proj.totalBudget;
            const progress = ((proj.spentAmount / proj.totalBudget) * 100).toFixed(1);
            const highRiskCount = proj.taxRecords.filter(r => r.riskLevel === '高危').length;
            const warningCount = proj.taxRecords.filter(r => r.riskLevel === '预警').length;

            return (
              <div
                key={proj.id}
                className="glass-panel rounded-xl overflow-hidden border border-[#444653]/40 hover:border-[#4cd7f6]/60 transition-all duration-200 group flex flex-col justify-between hover:shadow-[0_0_20px_rgba(76,215,246,0.15)]"
              >
                {/* 卡片头部 */}
                <div className="p-4 bg-[#171f33]/70 border-b border-[#444653]/30">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <span className="px-2 py-0.5 rounded bg-[#131b2e] border border-[#444653]/60 text-[11px] font-mono-num text-[#4cd7f6] font-semibold">
                          {proj.projectCode}
                        </span>
                        <span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                          proj.healthGrade.startsWith('甲级')
                            ? 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30'
                            : 'bg-[#F59E0B]/15 text-[#F59E0B] border-[#F59E0B]/30'
                        }`}>
                          {proj.healthGrade}
                        </span>
                      </div>
                      <h3 className="text-[16px] font-bold text-[#dae2fd] group-hover:text-[#4cd7f6] transition-colors leading-snug line-clamp-2">
                        {proj.name}
                      </h3>
                    </div>
                  </div>

                  <div className="mt-2.5 flex items-center gap-1.5 text-[12px] text-[#c4c5d5]">
                    <Layers className="w-3.5 h-3.5 text-[#4cd7f6] flex-shrink-0" />
                    <span className="truncate">{proj.constructionStage}</span>
                  </div>
                </div>

                {/* 卡片数据主体 */}
                <div className="p-4 space-y-3.5 flex-1">
                  {/* 核心财务指标三连 */}
                  <div className="grid grid-cols-3 gap-2 bg-[#131b2e]/60 p-2.5 rounded-lg border border-[#444653]/20 text-center">
                    <div>
                      <p className="text-[10px] text-[#8e909f]">总批复预算</p>
                      <p className="text-[13px] font-bold font-mono-num text-[#dae2fd] mt-0.5">
                        ¥ {(proj.totalBudget / 100000000).toFixed(2)}亿
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-[#8e909f]">已付开支</p>
                      <p className="text-[13px] font-bold font-mono-num text-[#ffa583] mt-0.5">
                        ¥ {(proj.spentAmount / 100000000).toFixed(2)}亿
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-[#8e909f]">剩余可用</p>
                      <p className="text-[13px] font-bold font-mono-num text-[#10B981] mt-0.5">
                        ¥ {(proj.remainingBudget / 100000000).toFixed(2)}亿
                      </p>
                    </div>
                  </div>

                  {/* 资金消耗进度条 */}
                  <div>
                    <div className="flex justify-between text-[11px] font-mono-num mb-1">
                      <span className="text-[#8e909f]">预算执行进度</span>
                      <span className={`font-bold ${isOverBudget ? 'text-[#EF4444]' : 'text-[#4cd7f6]'}`}>
                        {progress}% {isOverBudget ? '(超概算预警)' : ''}
                      </span>
                    </div>
                    <div className="w-full bg-[#131b2e] rounded-full h-2 overflow-hidden border border-[#444653]/30">
                      <div 
                        className={`h-full rounded-full transition-all duration-500 ${
                          isOverBudget
                            ? 'bg-[#EF4444]'
                            : 'bg-gradient-to-r from-[#1e40af] via-[#03b5d3] to-[#4cd7f6]'
                        }`}
                        style={{ width: `${Math.min(parseFloat(progress), 100)}%` }}
                      ></div>
                    </div>
                  </div>

                  {/* 涉及关联实体与风险统计 */}
                  <div className="flex items-center justify-between text-[11px] pt-1 text-[#8e909f] border-t border-[#444653]/20">
                    <div className="flex items-center gap-1.5">
                      <span>关联实体:</span>
                      <strong className="text-[#dae2fd] font-mono-num">{proj.taxRecords.length} 家</strong>
                    </div>
                    <div className="flex items-center gap-2 font-mono-num">
                      {highRiskCount > 0 && (
                        <span className="text-[#EF4444] bg-[#EF4444]/15 px-1.5 py-0.2 rounded border border-[#EF4444]/30 font-bold">
                          {highRiskCount} 高危
                        </span>
                      )}
                      {warningCount > 0 && (
                        <span className="text-[#F59E0B] bg-[#F59E0B]/15 px-1.5 py-0.2 rounded border border-[#F59E0B]/30 font-bold">
                          {warningCount} 预警
                        </span>
                      )}
                      {highRiskCount === 0 && warningCount === 0 && (
                        <span className="text-[#10B981] bg-[#10B981]/15 px-1.5 py-0.2 rounded border border-[#10B981]/30">
                          台账合规
                        </span>
                      )}
                    </div>
                  </div>

                  {/* 负责人与位置 */}
                  <div className="text-[11px] text-[#8e909f] space-y-1">
                    <div className="flex items-center gap-1.5 truncate">
                      <User className="w-3 h-3 text-[#4cd7f6] flex-shrink-0" />
                      <span className="truncate">{proj.managerName}</span>
                    </div>
                    <div className="flex items-center gap-1.5 truncate">
                      <MapPin className="w-3 h-3 text-[#8e909f] flex-shrink-0" />
                      <span className="truncate">{proj.location}</span>
                    </div>
                  </div>
                </div>

                {/* 卡片底部操作按钮 */}
                <div className="p-3.5 bg-[#0e172a]/60 border-t border-[#444653]/30 flex items-center justify-between gap-2">
                  <button
                    onClick={() => onSelectProject(proj.id)}
                    className="flex-1 bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] py-1.5 px-3 rounded-lg text-[12px] font-semibold transition-all border border-[#4cd7f6]/30 hover:border-[#4cd7f6] flex items-center justify-center gap-1.5 cursor-pointer shadow-sm"
                  >
                    <span>穿透业财详情</span>
                    <ChevronRight className="w-3.5 h-3.5 text-[#4cd7f6]" />
                  </button>

                  {onNavigateToAiReview && (
                    <button
                      onClick={() => onNavigateToAiReview(proj.id)}
                      className="bg-[#131b2e] hover:bg-[#222a3d] text-[#4cd7f6] hover:text-white p-2 rounded-lg border border-[#444653]/40 transition-colors cursor-pointer"
                      title="AI 专项审查"
                    >
                      <BrainCircuit className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* 表格列表视图 */
        <div className="glass-panel rounded-xl overflow-hidden border border-[#444653]/40">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="bg-[#171f33]/80 text-[#8e909f] border-b border-[#444653]/30 font-semibold select-none">
                <tr>
                  <th className="p-3.5 pl-4">工程编号</th>
                  <th className="p-3.5">示范工程名称</th>
                  <th className="p-3.5">当前施工阶段</th>
                  <th className="p-3.5 text-right">总批复概算</th>
                  <th className="p-3.5 text-right">累计支出</th>
                  <th className="p-3.5 text-center">资金进度</th>
                  <th className="p-3.5 text-center">健康评级</th>
                  <th className="p-3.5 text-center">关联涉税主体</th>
                  <th className="p-3.5 text-center pr-4">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#444653]/20">
                {filteredProjects.map(proj => {
                  const progress = ((proj.spentAmount / proj.totalBudget) * 100).toFixed(1);
                  return (
                    <tr key={proj.id} className="hover:bg-[#1e40af]/10 transition-colors group">
                      <td className="p-3.5 pl-4 font-mono-num text-[#4cd7f6] font-semibold">
                        {proj.projectCode}
                      </td>
                      <td className="p-3.5 font-bold text-[#dae2fd] max-w-[240px] truncate">
                        {proj.name}
                      </td>
                      <td className="p-3.5 text-[#c4c5d5] max-w-[200px] truncate">
                        {proj.constructionStage}
                      </td>
                      <td className="p-3.5 text-right font-mono-num text-[#dae2fd]">
                        ¥ {(proj.totalBudget / 100000000).toFixed(2)} 亿
                      </td>
                      <td className="p-3.5 text-right font-mono-num text-[#ffa583]">
                        ¥ {(proj.spentAmount / 100000000).toFixed(2)} 亿
                      </td>
                      <td className="p-3.5 text-center font-mono-num text-[#4cd7f6] font-bold">
                        {progress}%
                      </td>
                      <td className="p-3.5 text-center">
                        <span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${
                          proj.healthGrade.startsWith('甲级')
                            ? 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30'
                            : 'bg-[#F59E0B]/15 text-[#F59E0B] border-[#F59E0B]/30'
                        }`}>
                          {proj.healthGrade}
                        </span>
                      </td>
                      <td className="p-3.5 text-center font-mono-num text-[#dae2fd]">
                        {proj.taxRecords.length} 家
                      </td>
                      <td className="p-3.5 text-center pr-4">
                        <button
                          onClick={() => onSelectProject(proj.id)}
                          className="text-[#4cd7f6] hover:text-[#dae2fd] hover:underline font-semibold cursor-pointer"
                        >
                          穿透详情
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
