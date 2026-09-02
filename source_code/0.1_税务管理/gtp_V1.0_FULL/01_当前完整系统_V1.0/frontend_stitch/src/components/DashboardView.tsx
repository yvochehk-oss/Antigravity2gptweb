import { Building2, Download, ExternalLink, ShieldAlert, Wallet } from 'lucide-react';
import { DataStatusCard } from './DataStatusCard';
import { ProjectItem, DataStatus, RiskEvent, SystemSettings, EntityTaxLedgerRecord } from '../types';

interface DashboardViewProps {
  projects: ProjectItem[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onRetry: () => void;
  onSelectProject: (projectId: string) => void;
  onOpenRiskCenter: () => void;
  onOpenExportModal: () => void;
  riskEvents: RiskEvent[];
  riskStatus: DataStatus;
  riskStatusMessage: string;
  taxLedgerStatus: DataStatus;
  taxLedgerStatusMessage: string;
  taxLedgerRecords?: EntityTaxLedgerRecord[];
  settings?: SystemSettings;
}

function formatAmount(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return `¥ ${value.toLocaleString('zh-CN')} 元`;
}

export function DashboardView({
  projects,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onSelectProject,
  onOpenRiskCenter,
  onOpenExportModal,
  riskEvents,
  riskStatus,
  riskStatusMessage,
  taxLedgerStatus,
  taxLedgerStatusMessage,
  taxLedgerRecords = [],
}: DashboardViewProps) {
  const totalContract = projects.reduce((sum, project) => sum + project.totalBudget, 0);
  const totalCost = projects.reduce((sum, project) => sum + project.spentAmount, 0);
  const totalProfitBase = totalContract - totalCost;
  const taxLedgerAmount = taxLedgerRecords.reduce((sum, record) => sum + record.vatPayable, 0);
  const unresolvedRiskCount = riskEvents.filter(risk => risk.status !== '已闭环').length;
  const hasTaxLedgerData = taxLedgerStatus === 'READY' || taxLedgerRecords.length > 0;
  const hasRiskData = riskStatus === 'READY' || riskEvents.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight">集团经营总览</h2>
          <p className="text-[14px] text-[#c4c5d5] mt-1">仅展示 Tax API 已返回的项目经营数据；未接通的数据源不会显示演示金额。</p>
        </div>
        <button
          type="button"
          onClick={onOpenExportModal}
          disabled={projects.length === 0}
          className="bg-[#1e40af] hover:bg-[#1e40af]/80 disabled:opacity-40 disabled:cursor-not-allowed text-[#dde1ff] px-4 py-2 rounded-lg text-[13px] font-semibold border-t border-[#4cd7f6]/40 transition-colors flex items-center gap-2"
        >
          <Download className="w-4 h-4 text-[#4cd7f6]" />
          <span>导出当前真实数据</span>
        </button>
      </div>

      {dataStatus !== 'READY' || projects.length === 0 ? (
        <DataStatusCard status={dataStatus} title="项目经营数据状态" message={dataStatusMessage} onRetry={onRetry} />
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/20">
              <p className="text-[12px] text-[#8e909f]">Tax API 项目数</p>
              <p className="text-[28px] font-bold text-[#dae2fd] mt-2">{projects.length}<span className="text-[14px] font-normal text-[#8e909f] ml-1">个</span></p>
              <p className="text-[11px] text-[#10B981] mt-2">来源：/api/projects/{'{id}'}</p>
            </div>
            <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/20">
              <p className="text-[12px] text-[#8e909f]">合同总额（项目口径）</p>
              <p className="text-[21px] font-bold text-[#dae2fd] mt-2 break-all">{formatAmount(totalContract)}</p>
              <p className="text-[11px] text-[#8e909f] mt-2">由项目接口 contract_total 汇总</p>
            </div>
            <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/20">
              <p className="text-[12px] text-[#8e909f]">真实成本</p>
              <p className="text-[21px] font-bold text-[#b8c4ff] mt-2 break-all">{formatAmount(totalCost)}</p>
              <p className="text-[11px] text-[#8e909f] mt-2">由项目接口 real_cost 汇总</p>
            </div>
            <div className="glass-panel rounded-xl p-5 border border-[#F59E0B]/30">
              <p className="text-[12px] text-[#8e909f]">合同额减真实成本</p>
              <p className={`text-[21px] font-bold mt-2 break-all ${totalProfitBase < 0 ? 'text-[#EF4444]' : 'text-[#10B981]'}`}>{formatAmount(totalProfitBase)}</p>
              <p className="text-[11px] text-[#8e909f] mt-2">仅为接口口径差额，不替代利润/税务结论</p>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 glass-panel rounded-xl p-5 border border-[#444653]/30">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-[15px] font-bold text-[#dae2fd] flex items-center gap-2"><Building2 className="w-4 h-4 text-[#4cd7f6]" />已加载项目</h3>
                <span className="text-[11px] text-[#10B981]">真实接口数据</span>
              </div>
              <div className="space-y-2">
                {projects.map(project => (
                  <button
                    type="button"
                    key={project.id}
                    onClick={() => onSelectProject(project.id)}
                    className="w-full text-left rounded-lg border border-[#444653]/30 bg-[#131b2e]/70 p-3 hover:border-[#4cd7f6]/50 transition-colors"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-semibold text-[#dae2fd] truncate">{project.name}</p>
                        <p className="text-[11px] text-[#8e909f] mt-1">{project.projectCode || '未提供项目编码'} · {project.location}</p>
                      </div>
                      <div className="flex items-center gap-3 text-[11px] text-[#c4c5d5] flex-shrink-0">
                        <span>收入进度 {project.progressPercent.toFixed(1)}%</span>
                        <ExternalLink className="w-3.5 h-3.5 text-[#4cd7f6]" />
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-4">
              <div className="glass-panel rounded-xl p-5 border border-[#F59E0B]/30">
                <div className="flex items-center gap-2 text-[#F59E0B]"><Wallet className="w-4 h-4" /><h3 className="font-semibold text-[#dae2fd]">税务指标</h3></div>
                <div className="grid grid-cols-2 gap-3 mt-4">
                  <div>
                    <p className="text-[11px] text-[#8e909f]">台账记录数</p>
                    <p className="text-[22px] font-bold text-[#dae2fd] mt-1">{hasTaxLedgerData ? taxLedgerRecords.length : '—'}</p>
                  </div>
                  <div>
                    <p className="text-[11px] text-[#8e909f]">法人台账应纳 VAT 合计</p>
                    <p className="text-[17px] font-bold text-[#dae2fd] mt-1 break-all">{hasTaxLedgerData ? formatAmount(taxLedgerAmount) : '—'}</p>
                  </div>
                </div>
                <p className="text-[11px] text-[#8e909f] mt-3 leading-relaxed">按当前已加载法人月度台账记录汇总，不等同项目税负。</p>
                <p className="text-[11px] text-[#c4c5d5] mt-2 leading-relaxed">{taxLedgerStatusMessage}</p>
                <span className="inline-block mt-3 text-[10px] font-bold px-2 py-1 rounded border border-[#F59E0B]/30 text-[#F59E0B]">{taxLedgerStatus}</span>
              </div>
              <button type="button" onClick={onOpenRiskCenter} className="w-full glass-panel rounded-xl p-5 border border-[#EF4444]/30 text-left hover:bg-[#EF4444]/5 transition-colors">
                <div className="flex items-center gap-2 text-[#EF4444]"><ShieldAlert className="w-4 h-4" /><h3 className="font-semibold text-[#dae2fd]">风险中心</h3></div>
                <p className="text-[13px] text-[#c4c5d5] mt-3 leading-relaxed">
                  {hasRiskData ? `未闭环风险事件：${unresolvedRiskCount} 条` : '未闭环风险事件：—'}
                </p>
                <p className="text-[11px] text-[#c4c5d5] mt-2 leading-relaxed">{riskStatusMessage}</p>
                <span className="inline-block mt-3 text-[10px] font-bold px-2 py-1 rounded border border-[#EF4444]/30 text-[#EF4444]">{riskStatus}</span>
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
