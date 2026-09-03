import { useState } from 'react';
import { X, Download, Printer, FileText, AlertTriangle } from 'lucide-react';
import { ProjectItem } from '../types';

interface ExportReportModalProps {
  isOpen: boolean;
  onClose: () => void;
  projects: ProjectItem[];
}

function csvCell(value: unknown): string {
  const text = String(value ?? '');
  return `"${text.replaceAll('"', '""')}"`;
}

export function ExportReportModal({ isOpen, onClose, projects }: ExportReportModalProps) {
  const [error, setError] = useState('');
  if (!isOpen) return null;

  const downloadCsv = () => {
    if (projects.length === 0) {
      setError('没有可导出的真实项目数据。');
      return;
    }
    const rows = [
      ['项目 ID', '项目编码', '项目名称', '所在地', '合同总额', '真实成本', '剩余差额', '收入进度'],
      ...projects.map(project => [project.id, project.projectCode, project.name, project.location, project.totalBudget, project.spentAmount, project.remainingBudget, `${project.progressPercent}%`]),
    ];
    const csv = `\uFEFF${rows.map(row => row.map(csvCell).join(',')).join('\n')}`;
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `tax-project-summary-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    setError('');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
      <div className="surface-card flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-[var(--color-border)] p-6 shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-4"><div className="flex items-center gap-2"><FileText className="h-5 w-5 text-[var(--color-brand)]" /><h3 className="text-[18px] font-bold text-[var(--color-text-primary)]">真实项目摘要导出</h3></div><button type="button" onClick={onClose} className="text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"><X className="h-5 w-5" /></button></div>
        <div className="surface-2 my-3 flex-1 overflow-y-auto rounded-xl border border-[var(--color-border)] p-4 text-[13px]">
          <p className="mb-4 text-[var(--color-text-secondary)]">只导出当前 Tax API 已返回的项目级摘要。税务台账、风险和审计集合没有 JSON 接口时不会被填入报告。</p>
          {projects.length === 0 ? <div className="flex items-start gap-2 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 p-4 text-[var(--color-warning)]"><AlertTriangle className="h-5 w-5 flex-shrink-0" /><span>UNAVAILABLE：没有真实项目数据。</span></div> : <div className="overflow-x-auto"><table className="w-full border-collapse text-left text-[12px]"><thead><tr className="border-b border-[var(--color-border)] text-[var(--color-text-secondary)]"><th className="p-2">项目</th><th className="p-2 text-right">合同总额</th><th className="p-2 text-right">真实成本</th><th className="p-2 text-right">收入进度</th></tr></thead><tbody>{projects.map(project => <tr key={project.id} className="border-b border-[var(--color-border)]/70 transition-colors hover:bg-[var(--color-surface)]"><td className="p-2 text-[var(--color-text-primary)]">{project.projectCode} · {project.name}</td><td className="p-2 text-right text-[var(--color-text-primary)]">{project.totalBudget.toLocaleString('zh-CN')}</td><td className="p-2 text-right text-[var(--color-text-primary)]">{project.spentAmount.toLocaleString('zh-CN')}</td><td className="p-2 text-right text-[var(--color-text-primary)]">{project.progressPercent.toFixed(1)}%</td></tr>)}</tbody></table></div>}
        </div>
        {error && <p className="mb-3 text-[12px] text-[var(--color-danger)]">{error}</p>}
        <div className="flex items-center justify-end gap-3 border-t border-[var(--color-border)] pt-3"><button type="button" onClick={onClose} className="surface-2 rounded-lg border border-[var(--color-border)] px-4 py-2 text-[13px] font-semibold text-[var(--color-text-primary)]">关闭</button><button type="button" onClick={() => window.print()} disabled={projects.length === 0} className="surface-2 flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-4 py-2 text-[13px] font-semibold text-[var(--color-text-primary)] transition-colors hover:border-[var(--color-brand)] disabled:opacity-40"><Printer className="h-4 w-4 text-[var(--color-brand)]" />打印</button><button type="button" onClick={downloadCsv} disabled={projects.length === 0} className="flex items-center gap-1.5 rounded-lg bg-[var(--color-brand)] px-5 py-2 text-[13px] font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:opacity-40"><Download className="h-4 w-4" />下载 CSV</button></div>
      </div>
    </div>
  );
}
