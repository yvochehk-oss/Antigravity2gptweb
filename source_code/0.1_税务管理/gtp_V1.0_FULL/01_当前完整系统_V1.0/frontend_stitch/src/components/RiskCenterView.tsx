import { useState } from 'react';
import { 
  AlertOctagon, 
  AlertTriangle, 
  ShieldAlert, 
  CheckCircle2, 
  Clock, 
  ArrowRight, 
  Sparkles, 
  FileText,
  UserCheck,
  Filter
} from 'lucide-react';
import { RiskEvent, SystemSettings } from '../types';

interface RiskCenterViewProps {
  riskEvents: RiskEvent[];
  onResolveRisk: (id: string) => void;
  onAskAiAboutRisk: (entityName: string) => void;
  settings?: SystemSettings;
}

export function RiskCenterView({
  riskEvents,
  onResolveRisk,
  onAskAiAboutRisk,
  settings
}: RiskCenterViewProps) {
  const [activeFilter, setActiveFilter] = useState<'全部' | '高危' | '中度' | '轻度'>('全部');
  const [selectedRisk, setSelectedRisk] = useState<RiskEvent | null>(null);

  const crossRegionThreshold = settings?.crossRegionTaxThreshold ?? 5;
  const budgetStopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;

  const filtered = riskEvents.filter(r => {
    if (activeFilter === '全部') return true;
    return r.severity === activeFilter;
  });

  return (
    <div className="space-y-6">
      {/* 顶部标题与应急看板 */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight flex items-center gap-2">
            <ShieldAlert className="w-7 h-7 text-[#EF4444]" />
            <span>风控预警与稽查处置中心</span>
          </h2>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            动态监测大额跨期暂估、跨区施工预缴核销 (偏差阈值 ≥{crossRegionThreshold}%)、四流合一比对及工程造价超概算 (止付阈值 ≥{budgetStopPayThreshold}%)。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {(['全部', '高危', '中度', '轻度'] as const).map((lvl) => (
            <button
              key={lvl}
              onClick={() => setActiveFilter(lvl)}
              className={`px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all cursor-pointer ${
                activeFilter === lvl
                  ? lvl === '高危'
                    ? 'bg-[#EF4444] text-white shadow-[0_0_12px_#EF4444]'
                    : 'bg-[#1e40af] text-[#dde1ff] border border-[#4cd7f6]/40'
                  : 'bg-[#171f33] text-[#8e909f] hover:text-[#dae2fd]'
              }`}
            >
              {lvl}
            </button>
          ))}
        </div>
      </div>

      {/* 风险事件卡片流 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {filtered.map((risk) => {
          const isHigh = risk.severity === '高危';
          const isResolved = risk.status === '已闭环';

          return (
            <div
              key={risk.id}
              className={`glass-panel rounded-xl p-5 flex flex-col justify-between gap-4 transition-all ${
                isHigh ? 'border-[#EF4444]/60 glow-red bg-[#171f33]/90' : 'border-[#F59E0B]/50 glow-amber'
              }`}
            >
              <div>
                {/* 标头 */}
                <div className="flex justify-between items-start gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                      isHigh ? 'bg-[#EF4444]/20 text-[#ffb4ab] border border-[#EF4444]/40 animate-pulse' : 'bg-[#F59E0B]/20 text-[#ffa583] border border-[#F59E0B]/40'
                    }`}>
                      {risk.severity}风险 · {risk.riskType}
                    </span>
                    <span className="text-[11px] font-mono-num text-[#8e909f] flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {risk.triggerTime}
                    </span>
                  </div>
                  <span className="text-[12px] font-bold text-[#dae2fd] bg-[#131b2e] px-2.5 py-1 rounded border border-[#444653]/30">
                    {risk.status}
                  </span>
                </div>

                {/* 涉及项目与主体 */}
                <h4 className="text-[16px] font-bold text-[#dae2fd]">
                  {risk.projectName}
                </h4>
                <p className="text-[13px] text-[#4cd7f6] font-medium mt-0.5">
                  涉税关联主体: {risk.entityName}
                </p>

                {/* 风险详细描述 */}
                <div className="my-3 p-3 rounded-lg bg-[#0b1326]/70 border border-[#444653]/30 text-[12px] text-[#dae2fd] leading-relaxed">
                  <p className="font-semibold text-[#ffa583] mb-1">风险触发诱因：</p>
                  {risk.description}
                </div>

                {/* 审计应对建议 */}
                <div className="p-3 rounded-lg bg-[#1e40af]/15 border border-[#4cd7f6]/20 text-[12px] text-[#c4c5d5]">
                  <p className="font-semibold text-[#4cd7f6] mb-1 flex items-center gap-1">
                    <Sparkles className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    <span>智能审计处置建议：</span>
                  </p>
                  {risk.auditSuggestions}
                </div>
              </div>

              {/* 底部经办人与处置操作 */}
              <div className="pt-3 border-t border-[#444653]/30 flex justify-between items-center text-[12px]">
                <div className="flex items-center gap-1.5 text-[#8e909f]">
                  <UserCheck className="w-4 h-4 text-[#4cd7f6]" />
                  <span>责任专员: <strong className="text-[#dae2fd]">{risk.handler}</strong></span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onAskAiAboutRisk(risk.entityName)}
                    className="px-3 py-1.5 rounded-lg bg-[#03b5d3]/15 hover:bg-[#03b5d3]/25 text-[#4cd7f6] text-[12px] font-semibold border border-[#4cd7f6]/40 cursor-pointer"
                  >
                    呼叫智能助手协助取证
                  </button>
                  {!isResolved && (
                    <button
                      onClick={() => onResolveRisk(risk.id)}
                      className="px-3 py-1.5 rounded-lg bg-[#10B981] hover:bg-[#10B981]/80 text-[#0b1326] text-[12px] font-bold transition-colors cursor-pointer"
                    >
                      完成闭环整改
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
