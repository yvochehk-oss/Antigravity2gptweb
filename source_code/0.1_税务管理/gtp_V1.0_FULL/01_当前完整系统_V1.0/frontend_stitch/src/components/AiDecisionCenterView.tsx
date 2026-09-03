import { useState } from 'react';
import { BrainCircuit } from 'lucide-react';
import type { DataStatus, ProjectItem } from '../types';
import { AiReviewView } from './AiReviewView';
import { TaxPlanningView } from './TaxPlanningView';

interface AiDecisionCenterViewProps {
  projects: ProjectItem[];
  dataStatus: DataStatus;
  selectedProjectId?: string;
  onSelectProject?: (id: string) => void;
  onAskAiAboutRisk?: (entityName: string) => void;
  reviewResult?: Record<string, unknown> | null;
}

type WorkspaceTab = 'review' | 'planning';

function reviewDataSource(result: Record<string, unknown> | null | undefined): string | null {
  if (!result) return null;
  if (typeof result.data_source === 'string') return result.data_source;

  for (const key of ['result', 'consensus']) {
    const nested = result[key];
    if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
      const dataSource = (nested as Record<string, unknown>).data_source;
      if (typeof dataSource === 'string') return dataSource;
    }
  }

  return null;
}

export function AiDecisionCenterView({
  projects,
  dataStatus,
  selectedProjectId,
  onSelectProject,
  onAskAiAboutRisk,
  reviewResult,
}: AiDecisionCenterViewProps) {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('review');
  const isCanonicalReview = reviewDataSource(reviewResult) === 'CANONICAL_FACTS';

  return (
    <div className="space-y-6">
      <header data-page-title="ai-decision" className="w-full">
        <div className="flex items-start gap-2.5">
          <BrainCircuit className="mt-1 h-7 w-7 flex-shrink-0 text-[#a78bfa]" />
          <div>
            <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">智能财税决策中心</h2>
            <p className="mt-1 text-[13px] text-[#8e909f]">综合体检工作区解释真实事实；项目筹划沙盘仅承载用户假设下的模拟方案。</p>
          </div>
        </div>
      </header>

      <div data-page-controls="ai-decision" className="glass-panel rounded-xl border border-[#444653]/30 p-3.5">
        <div role="tablist" aria-label="智能财税决策中心工作区" className="flex w-fit flex-wrap gap-2 rounded-xl border border-[#444653]/40 bg-[#131b2e] p-1">
          <button
            id="ai-decision-review-tab"
            type="button"
            role="tab"
            aria-selected={activeTab === 'review'}
            aria-controls="ai-decision-review-panel"
            onClick={() => setActiveTab('review')}
            className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'review' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}
          >
            综合体检与辅助研判
          </button>
          <button
            id="ai-decision-planning-tab"
            type="button"
            role="tab"
            aria-selected={activeTab === 'planning'}
            aria-controls="ai-decision-planning-panel"
            onClick={() => setActiveTab('planning')}
            className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'planning' ? 'bg-[#6d28d9] text-[#f5f3ff]' : 'text-[#8e909f]'}`}
          >
            项目筹划沙盘
          </button>
        </div>
      </div>

      <section
        id="ai-decision-review-panel"
        role="tabpanel"
        aria-labelledby="ai-decision-review-tab"
        data-testid="review-workspace"
        hidden={activeTab !== 'review'}
        className="space-y-5"
      >
        {isCanonicalReview ? (
          <div className="rounded-xl border border-[#10B981]/40 bg-[#10B981]/10 px-4 py-3" role="status">
            <p className="text-[13px] font-bold text-[#6ee7b7]">基于规范事实</p>
            <p className="mt-1 text-[12px] text-[#a7f3d0]">后端智能审查已明确确认当前结果基于规范事实数据源。</p>
          </div>
        ) : (
          <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/10 px-4 py-3" role="status">
            <p className="text-[13px] font-bold text-[#F59E0B]">降级运行：当前智能审查上下文尚未确认使用规范事实模式</p>
            <p className="mt-1 text-[12px] text-[#fcd34d]">仅当后端审查结果明确返回规范事实数据源时，才标记为“基于规范事实”。</p>
          </div>
        )}

        <AiReviewView projects={projects} dataStatus={dataStatus} onAskAiAboutRisk={onAskAiAboutRisk} />
      </section>

      <section
        id="ai-decision-planning-panel"
        role="tabpanel"
        aria-labelledby="ai-decision-planning-tab"
        data-testid="planning-workspace"
        hidden={activeTab !== 'planning'}
        className="space-y-5"
      >
        <div className="rounded-xl border border-[#F59E0B]/50 bg-[#F59E0B]/10 px-4 py-3" role="note">
          <p className="text-[13px] font-bold text-[#F59E0B]">模拟方案 · 非申报依据</p>
          <p className="mt-1 text-[12px] text-[#fcd34d]">本视图基于用户输入假设，不代表项目真实经营结果，不属于法人法定申报依据。</p>
        </div>

        <TaxPlanningView
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={onSelectProject}
          onAskAiAboutRisk={onAskAiAboutRisk}
        />
      </section>
    </div>
  );
}
