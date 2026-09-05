import { useState } from 'react';
import { Building2, Database, Download, ExternalLink, Landmark, LayoutDashboard, ShieldAlert } from 'lucide-react';
import { DataStatusCard } from './DataStatusCard';
import { ProjectItem, DataStatus, RiskEvent, SystemSettings, EntityTaxLedgerRecord } from '../types';
import { dataStatusLabel } from './uiLocalization';
import { fetchExcelExport } from '../excelExportApi';

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

function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function StatutoryKpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="surface-card rounded-xl border border-default p-4">
      <p className="text-[11px] text-secondary">{label}</p>
      <p className="mt-2 break-all text-[19px] font-bold text-primary">{value}</p>
    </div>
  );
}

function ProjectKpi({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="surface-card rounded-xl border border-default p-4">
      <p className="text-[11px] text-secondary">{label}</p>
      <p className="mt-2 break-all text-[19px] font-bold text-primary">{value}</p>
      <p className="mt-2 text-[10px] leading-relaxed text-secondary">{note}</p>
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
  riskEvents,
  riskStatus,
  riskStatusMessage,
  taxLedgerStatus,
  taxLedgerStatusMessage,
  taxLedgerRecords = [],
}: DashboardViewProps) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
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
  const periods = taxLedgerRecords.map(record => record.period).filter(Boolean).sort();
  const exportPeriod = periods.length > 0 ? periods[periods.length - 1] : currentPeriod();

  const exportWorkbook = async () => {
    setExporting(true);
    setExportError('');
    try {
      await fetchExcelExport('ALL', 'current', exportPeriod);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : 'Excel 导出失败。');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <header data-page-title="dashboard" className="w-full">
        <div className="flex items-start gap-2.5">
          <LayoutDashboard className="mt-1 h-7 w-7 flex-shrink-0 text-brand" />
          <div>
            <h2 className="text-[28px] font-bold tracking-tight text-primary">集团经营总览</h2>
            <p className="mt-1 text-[14px] text-secondary">法人法定申报事实与项目工程管理口径分区展示，禁止跨域混算。</p>
          </div>
        </div>
      </header>

      <div data-page-controls="dashboard" className="surface-card flex flex-wrap items-center justify-end gap-3 rounded-xl border border-default p-3.5">
        {exportError && <span className="mr-auto text-[12px] text-[var(--color-danger)]">{exportError}</span>}
        <button
          type="button"
          onClick={() => void exportWorkbook()}
          disabled={exporting || (projects.length === 0 && taxLedgerRecords.length === 0)}
          className="flex items-center gap-2 rounded-lg border border-[var(--color-brand)] bg-[var(--color-brand)] px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Download className="h-4 w-4" />
          <span>{exporting ? '正在生成 Excel…' : '导出多 Sheet Excel'}</span>
        </button>
      </div>

      <section aria-label="法人主体法定税务总览" className="surface-card space-y-4 rounded-2xl border border-default p-5">
        <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
          <div>
            <div className="flex items-center gap-2 text-brand">
              <Landmark className="h-5 w-5" />
              <h3 className="text-[17px] font-bold text-primary">法人主体法定申报增值税总览</h3>
            </div>
            <p className="mt-1 text-[12px] font-semibold text-secondary">法人法定申报 · 法定申报口径</p>
          </div>
          <button
            type="button"
            onClick={() => onOpenEntityCorporate(entityCodes[0])}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-[12px] font-semibold text-primary transition-colors hover:border-[var(--color-brand)] hover:text-brand"
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
        <p className="text-[11px] leading-relaxed text-secondary">权威来源：法人增值税法定台账。本区域不展示项目增值税管理净头寸。</p>
      </section>

      <section aria-label="项目工程管理口径总览" className="surface-card space-y-4 rounded-2xl border border-default p-5">
        <div>
          <div className="flex items-center gap-2 text-brand">
            <Building2 className="h-5 w-5" />
            <h3 className="text-[17px] font-bold text-primary">项目工程管理边界总览</h3>
          </div>
          <p className="mt-1 text-[12px] font-semibold text-secondary">项目管理边界 · 项目管理／测算口径</p>
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

        <div className="rounded-xl border border-default bg-[var(--color-surface-2)] p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="flex items-center gap-2 text-[14px] font-bold text-primary">
              <Building2 className="h-4 w-4 text-brand" />
              项目工程列表
            </h4>
            <span className="text-[10px] text-secondary">点击进入项目管理边界详情</span>
          </div>
          <div className="space-y-2">
            {projects.length === 0 ? (
              <p className="rounded-lg border border-default px-3 py-5 text-center text-[12px] text-secondary">当前没有已加载项目。</p>
            ) : projects.map(project => (
              <button
                type="button"
                key={project.id}
                onClick={() => onSelectProject(project.id)}
                className="w-full rounded-lg border border-default bg-[var(--color-surface)] p-3 text-left transition-colors hover:border-[var(--color-brand)]"
              >
                <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-primary">{project.name}</p>
                    <p className="mt-1 text-[11px] text-secondary">{project.projectCode || '未提供项目编码'} · {project.location}</p>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-3 text-[11px] text-secondary">
                    <span>进度 {project.progressPercent.toFixed(1)}%</span>
                    <ExternalLink className="h-3.5 w-3.5 text-brand" />
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
          className="surface-card rounded-xl border border-default p-5 text-left transition-colors hover:border-[var(--color-danger)]"
        >
          <div className="flex items-center gap-2 text-[var(--color-danger)]"><ShieldAlert className="h-4 w-4" /><h3 className="font-semibold text-primary">风险中心</h3></div>
          <p className="mt-3 text-[13px] leading-relaxed text-secondary">{hasRiskData ? `未闭环风险事件：${unresolvedRiskCount} 条` : '未闭环风险事件：—'}</p>
          <p className="mt-2 text-[11px] leading-relaxed text-secondary">{riskStatusMessage}</p>
          <span className="mt-3 inline-block rounded border border-[var(--color-danger)] px-2 py-1 text-[10px] font-bold text-[var(--color-danger)]">{dataStatusLabel(riskStatus)}</span>
        </button>

        <div className="surface-card rounded-xl border border-default p-5">
          <div className="flex items-center gap-2 text-brand"><Database className="h-4 w-4 text-brand" /><h3 className="font-semibold text-primary">数据源健康状态</h3></div>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-default bg-[var(--color-surface-2)] p-3">
              <p className="text-[11px] text-secondary">法人法定申报增值税</p>
              <p className="mt-1 text-[13px] font-semibold text-primary">{dataStatusLabel(taxLedgerStatus)}</p>
              <p className="mt-1 text-[10px] text-secondary">法人增值税法定台账</p>
            </div>
            <div className="rounded-lg border border-default bg-[var(--color-surface-2)] p-3">
              <p className="text-[11px] text-secondary">项目管理边界</p>
              <p className="mt-1 text-[13px] font-semibold text-primary">{dataStatusLabel(dataStatus)}</p>
              <p className="mt-1 text-[10px] text-secondary">项目真实接口／规范经营投影</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
