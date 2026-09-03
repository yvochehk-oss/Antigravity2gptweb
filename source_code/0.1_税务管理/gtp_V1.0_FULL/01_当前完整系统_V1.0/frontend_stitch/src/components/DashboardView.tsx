import { Building2, Database, Download, ExternalLink, Landmark, LayoutDashboard, ShieldAlert } from 'lucide-react';
import { DataStatusCard } from './DataStatusCard';
import { ProjectItem, DataStatus, RiskEvent, SystemSettings, EntityTaxLedgerRecord } from '../types';
import { dataStatusLabel } from './uiLocalization';

interface DashboardViewProps {
  projects: ProjectItem[];
  dataStatus: DataStatus;
  dataStatusMessage: string;
  onRetry: () => void;
  onSelectProject: (projectId: string) => void;
  onOpenEntityCorporate: (entityCode?: string) => void;
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

function StatutoryKpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[#4cd7f6]/20 bg-[#03b5d3]/5 p-4">
      <p className="text-[11px] text-[#8e909f]">{label}</p>
      <p className="mt-2 break-all text-[19px] font-bold text-[#dde1ff]">{value}</p>
    </div>
  );
}

function ProjectKpi({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="glass-panel rounded-xl border border-[#8b5cf6]/20 p-4">
      <p className="text-[11px] text-[#8e909f]">{label}</p>
      <p className="mt-2 break-all text-[19px] font-bold text-[#dae2fd]">{value}</p>
      <p className="mt-2 text-[10px] leading-relaxed text-[#8e909f]">{note}</p>
    </div>
  );
}

export function DashboardView({
  projects,
  dataStatus,
  dataStatusMessage,
  onRetry,
  onSelectProject,
  onOpenEntityCorporate,
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
  const contractCostGap = totalContract - totalCost;

  const entityCodes = [...new Set(taxLedgerRecords.map(record => record.entityCode).filter(Boolean))];
  const totalOutputVat = taxLedgerRecords.reduce((sum, record) => sum + record.outputVat, 0);
  const totalInputVat = taxLedgerRecords.reduce((sum, record) => sum + record.inputVat, 0);
  const totalVatPayableAfterPrepayment = taxLedgerRecords.reduce(
    (sum, record) => sum + record.vatPayableAfterPrepayment,
    0,
  );
  const totalClosingInputCredit = taxLedgerRecords.reduce((sum, record) => sum + record.closingInputCredit, 0);

  const unresolvedRiskCount = riskEvents.filter(risk => risk.status !== '已闭环').length;
  const hasStatutoryData = taxLedgerRecords.length > 0;
  const hasRiskData = riskStatus === 'READY' || riskEvents.length > 0;

  return (
    <div className="space-y-6">
      <header data-page-title="dashboard" className="w-full">
        <div className="flex items-start gap-2.5">
          <LayoutDashboard className="mt-1 h-7 w-7 flex-shrink-0 text-[#4cd7f6]" />
          <div>
            <h2 className="text-[28px] font-bold tracking-tight text-[#dae2fd]">集团经营总览</h2>
            <p className="mt-1 text-[14px] text-[#c4c5d5]">法人法定申报事实与项目工程管理口径分区展示，禁止跨域混算。</p>
          </div>
        </div>
      </header>

      <div data-page-controls="dashboard" className="glass-panel flex flex-wrap items-center justify-end gap-3 rounded-xl border border-[#444653]/30 p-3.5">
        <button
          type="button"
          onClick={onOpenExportModal}
          disabled={projects.length === 0 && taxLedgerRecords.length === 0}
          className="flex items-center gap-2 rounded-lg border-t border-[#4cd7f6]/40 bg-[#1e40af] px-4 py-2 text-[13px] font-semibold text-[#dde1ff] transition-colors hover:bg-[#1e40af]/80 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Download className="h-4 w-4 text-[#4cd7f6]" />
          <span>导出当前真实数据</span>
        </button>
      </div>

      <section aria-label="法人主体法定税务总览" className="space-y-4 rounded-2xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-5">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
          <div>
            <div className="flex items-center gap-2 text-[#4cd7f6]">
              <Landmark className="h-5 w-5" />
              <h3 className="text-[17px] font-bold text-[#dae2fd]">法人主体法定申报增值税总览</h3>
            </div>
            <p className="mt-1 text-[12px] font-semibold text-[#4cd7f6]">法人法定申报 · 法定申报口径</p>
          </div>
          <button
            type="button"
            onClick={() => onOpenEntityCorporate(entityCodes[0])}
            className="rounded-lg border border-[#4cd7f6]/30 bg-[#03b5d3]/10 px-3 py-2 text-[12px] font-semibold text-[#4cd7f6] hover:bg-[#03b5d3]/20"
          >
            进入法人经营画像
          </button>
        </div>

        {taxLedgerStatus !== 'READY' && (
          <DataStatusCard status={taxLedgerStatus} title="法人法定增值税数据状态" message={taxLedgerStatusMessage} />
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <StatutoryKpi label="法人主体数量" value={hasStatutoryData ? `${entityCodes.length} 个` : '—'} />
          <StatutoryKpi label="本期法定销项税额合计" value={hasStatutoryData ? formatAmount(totalOutputVat) : '—'} />
          <StatutoryKpi label="本期法定进项税额合计" value={hasStatutoryData ? formatAmount(totalInputVat) : '—'} />
          <StatutoryKpi label="本期法定应纳税额合计" value={hasStatutoryData ? formatAmount(totalVatPayableAfterPrepayment) : '—'} />
          <StatutoryKpi label="期末留抵税额合计" value={hasStatutoryData ? formatAmount(totalClosingInputCredit) : '—'} />
        </div>
        <p className="text-[11px] leading-relaxed text-[#8e909f]">权威来源：法人增值税法定台账。本区域不展示项目增值税管理净头寸。</p>
      </section>

      <section aria-label="项目工程管理口径总览" className="space-y-4 rounded-2xl border border-[#8b5cf6]/30 bg-[#8b5cf6]/5 p-5">
        <div>
          <div className="flex items-center gap-2 text-[#c4b5fd]">
            <Building2 className="h-5 w-5" />
            <h3 className="text-[17px] font-bold text-[#dae2fd]">项目工程管理边界总览</h3>
          </div>
          <p className="mt-1 text-[12px] font-semibold text-[#c4b5fd]">项目管理边界 · 项目管理／测算口径</p>
        </div>

        {dataStatus !== 'READY' && (
          <DataStatusCard status={dataStatus} title="项目经营数据状态" message={dataStatusMessage} onRetry={onRetry} />
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <ProjectKpi label="项目接口项目数量" value={`${projects.length} 个`} note="来源：项目真实接口" />
          <ProjectKpi label="项目外部收入总额（合同额）" value={formatAmount(totalContract)} note="由项目接口合同总额汇总" />
          <ProjectKpi label="真实归集成本" value={formatAmount(totalCost)} note="由项目接口真实成本汇总" />
          <ProjectKpi
            label="合同额与真实成本差额"
            value={formatAmount(contractCostGap)}
            note="仅为合同与成本差额，非最终利润"
          />
        </div>

        <div className="rounded-xl border border-[#444653]/30 bg-[#171f33]/45 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="flex items-center gap-2 text-[14px] font-bold text-[#dae2fd]">
              <Building2 className="h-4 w-4 text-[#c4b5fd]" />
              项目工程列表
            </h4>
            <span className="text-[10px] text-[#8e909f]">点击进入项目管理边界详情</span>
          </div>
          <div className="space-y-2">
            {projects.length === 0 ? (
              <p className="rounded-lg border border-[#444653]/20 px-3 py-5 text-center text-[12px] text-[#8e909f]">当前没有已加载项目。</p>
            ) : projects.map(project => (
              <button
                type="button"
                key={project.id}
                onClick={() => onSelectProject(project.id)}
                className="w-full rounded-lg border border-[#444653]/30 bg-[#131b2e]/70 p-3 text-left transition-colors hover:border-[#8b5cf6]/50"
              >
                <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-[#dae2fd]">{project.name}</p>
                    <p className="mt-1 text-[11px] text-[#8e909f]">{project.projectCode || '未提供项目编码'} · {project.location}</p>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-3 text-[11px] text-[#c4c5d5]">
                    <span>进度 {project.progressPercent.toFixed(1)}%</span>
                    <ExternalLink className="h-3.5 w-3.5 text-[#c4b5fd]" />
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section aria-label="风险与数据源健康状态" className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <button
          type="button"
          onClick={onOpenRiskCenter}
          className="glass-panel rounded-xl border border-[#EF4444]/30 p-5 text-left transition-colors hover:bg-[#EF4444]/5"
        >
          <div className="flex items-center gap-2 text-[#EF4444]"><ShieldAlert className="h-4 w-4" /><h3 className="font-semibold text-[#dae2fd]">风险中心</h3></div>
          <p className="mt-3 text-[13px] leading-relaxed text-[#c4c5d5]">{hasRiskData ? `未闭环风险事件：${unresolvedRiskCount} 条` : '未闭环风险事件：—'}</p>
          <p className="mt-2 text-[11px] leading-relaxed text-[#c4c5d5]">{riskStatusMessage}</p>
          <span className="mt-3 inline-block rounded border border-[#EF4444]/30 px-2 py-1 text-[10px] font-bold text-[#EF4444]">{dataStatusLabel(riskStatus)}</span>
        </button>

        <div className="glass-panel rounded-xl border border-[#444653]/30 p-5">
          <div className="flex items-center gap-2 text-[#4cd7f6]"><Database className="h-4 w-4" /><h3 className="font-semibold text-[#dae2fd]">数据源健康状态</h3></div>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-[#444653]/25 bg-[#131b2e]/70 p-3">
              <p className="text-[11px] text-[#8e909f]">法人法定申报增值税</p>
              <p className="mt-1 text-[13px] font-semibold text-[#dae2fd]">{dataStatusLabel(taxLedgerStatus)}</p>
              <p className="mt-1 text-[10px] text-[#8e909f]">法人增值税法定台账</p>
            </div>
            <div className="rounded-lg border border-[#444653]/25 bg-[#131b2e]/70 p-3">
              <p className="text-[11px] text-[#8e909f]">项目管理边界</p>
              <p className="mt-1 text-[13px] font-semibold text-[#dae2fd]">{dataStatusLabel(dataStatus)}</p>
              <p className="mt-1 text-[10px] text-[#8e909f]">项目真实接口／规范经营投影</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
