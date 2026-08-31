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
    <div className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-4">
      <div className="bg-[#171f33] border border-[#4cd7f6]/40 rounded-xl max-w-3xl w-full p-6 shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        <div className="flex justify-between items-center pb-4 border-b border-[#444653]/40"><div className="flex items-center gap-2"><FileText className="w-5 h-5 text-[#4cd7f6]" /><h3 className="text-[18px] font-bold text-[#dae2fd]">真实项目摘要导出</h3></div><button type="button" onClick={onClose} className="text-[#8e909f] hover:text-[#dae2fd]"><X className="w-5 h-5" /></button></div>
        <div className="flex-1 overflow-y-auto p-4 my-3 bg-[#0b1326] border border-[#444653]/40 rounded-xl text-[13px]">
          <p className="text-[#c4c5d5] mb-4">只导出当前 Tax API 已返回的项目级摘要。税务台账、风险和审计集合没有 JSON 接口时不会被填入报告。</p>
          {projects.length === 0 ? <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 p-4 text-[#ffd0a8] flex items-start gap-2"><AlertTriangle className="w-5 h-5 flex-shrink-0" /><span>UNAVAILABLE：没有真实项目数据。</span></div> : <div className="overflow-x-auto"><table className="w-full text-left border-collapse text-[12px]"><thead><tr className="text-[#8e909f] border-b border-[#444653]/30"><th className="p-2">项目</th><th className="p-2 text-right">合同总额</th><th className="p-2 text-right">真实成本</th><th className="p-2 text-right">收入进度</th></tr></thead><tbody>{projects.map(project => <tr key={project.id} className="border-b border-[#444653]/20"><td className="p-2 text-[#dae2fd]">{project.projectCode} · {project.name}</td><td className="p-2 text-right">{project.totalBudget.toLocaleString('zh-CN')}</td><td className="p-2 text-right">{project.spentAmount.toLocaleString('zh-CN')}</td><td className="p-2 text-right">{project.progressPercent.toFixed(1)}%</td></tr>)}</tbody></table></div>}
        </div>
        {error && <p className="text-[12px] text-[#ffb4ab] mb-3">{error}</p>}
        <div className="flex justify-end items-center gap-3 pt-3 border-t border-[#444653]/40"><button type="button" onClick={onClose} className="px-4 py-2 bg-[#2d3449] text-[#dae2fd] text-[13px] font-semibold rounded-lg">关闭</button><button type="button" onClick={() => window.print()} disabled={projects.length === 0} className="px-4 py-2 bg-[#1e40af]/60 text-[#dde1ff] text-[13px] font-semibold rounded-lg border border-[#4cd7f6]/30 flex items-center gap-1.5 disabled:opacity-40"><Printer className="w-4 h-4 text-[#4cd7f6]" />打印</button><button type="button" onClick={downloadCsv} disabled={projects.length === 0} className="px-5 py-2 bg-[#03b5d3] text-[#001f26] text-[13px] font-bold rounded-lg flex items-center gap-1.5 disabled:opacity-40"><Download className="w-4 h-4" />下载 CSV</button></div>
      </div>
    </div>
  );
}

/* ===========================================================
   保持 Windows ClearType 亚像素渲染（系统字体方案下无需 swap）
   -webkit-font-smoothing: auto  -> Windows 使用 ClearType
                                -> macOS  使用视网膜灰度平滑
   =========================================================== */
*, *::before, *::after, html, body, .antialiased {
  -webkit-font-smoothing: auto !important;
  -moz-osx-font-smoothing: auto !important;
  text-rendering: auto !important;
}
