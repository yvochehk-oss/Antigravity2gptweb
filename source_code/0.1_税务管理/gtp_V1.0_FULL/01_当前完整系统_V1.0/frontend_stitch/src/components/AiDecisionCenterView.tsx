import { useState } from 'react';
import type { DataStatus, ProjectItem } from '../types';
import { AiReviewView } from './AiReviewView';
import { TaxPlanningView } from './TaxPlanningView';

interface AiDecisionCenterViewProps {
  projects: ProjectItem[];
  dataStatus: DataStatus;
  selectedProjectId?: string;
  onSelectProject?: (id: string) => void;
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
  reviewResult,
}: AiDecisionCenterViewProps) {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('review');
  const isCanonicalReview = reviewDataSource(reviewResult) === 'CANONICAL_FACTS';

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[26px] font-bold text-[#dae2fd]">AI 决策中心</h2>
        <p className="text-[13px] text-[#8e909f] mt-1">
          Review Workspace 解释真实事实；Planning Workspace 仅承载用户假设下的模拟方案。
        </p>
      </div>

      <div
        role="tablist"
        aria-label="AI 决策中心工作区"
        className="flex flex-wrap gap-2 p-1 bg-[#131b2e] rounded-xl border border-[#444653]/40 w-fit"
      >
        <button
          id="ai-decision-review-tab"
          type="button"
          role="tab"
          aria-selected={activeTab === 'review'}
          aria-controls="ai-decision-review-panel"
          onClick={() => setActiveTab('review')}
          className={`px-4 py-2 rounded-lg text-[13px] font-semibold ${activeTab === 'review' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}
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
          className={`px-4 py-2 rounded-lg text-[13px] font-semibold ${activeTab === 'planning' ? 'bg-[#6d28d9] text-[#f5f3ff]' : 'text-[#8e909f]'}`}
        >
          项目筹划沙盘
        </button>
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
            <p className="text-[13px] font-bold text-[#6ee7b7]">FACT-BASED · CANONICAL_FACTS</p>
            <p className="text-[12px] text-[#a7f3d0] mt-1">后端 AI Review 已明确确认 data_source === 'CANONICAL_FACTS'。</p>
          </div>
        ) : (
          <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/10 px-4 py-3" role="status">
            <p className="text-[13px] font-bold text-[#F59E0B]">DEGRADED：当前 AI Review 上下文未确认处于 Canonical 模式</p>
            <p className="text-[12px] text-[#fcd34d] mt-1">仅当后端审查结果明确返回 data_source === 'CANONICAL_FACTS' 时才展示 FACT-BASED。</p>
          </div>
        )}

        <AiReviewView projects={projects} dataStatus={dataStatus} />
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
          <p className="text-[13px] font-bold text-[#F59E0B]">SCENARIO · 模拟方案 | SIMULATION · NOT FILING BASIS</p>
          <p className="text-[12px] text-[#fcd34d] mt-1">本视图基于用户输入假设，不代表项目真实经营结果，不属于法人法定申报依据。</p>
        </div>

        <TaxPlanningView
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={onSelectProject}
        />
      </section>
    </div>
  );
}
