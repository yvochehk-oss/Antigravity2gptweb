import { useState } from 'react';
import { X, Download, Printer, CheckCircle, FileText, Building2, ShieldCheck } from 'lucide-react';
import { ProjectItem } from '../types';

interface ExportReportModalProps {
  isOpen: boolean;
  onClose: () => void;
  projects: ProjectItem[];
}

export function ExportReportModal({ isOpen, onClose, projects }: ExportReportModalProps) {
  const [isExporting, setIsExporting] = useState(false);
  const [exportSuccess, setExportSuccess] = useState(false);

  if (!isOpen) return null;

  const handlePrint = () => {
    window.print();
  };

  const handleDownload = () => {
    setIsExporting(true);
    setTimeout(() => {
      setIsExporting(false);
      setExportSuccess(true);
      setTimeout(() => setExportSuccess(false), 3000);
    }, 1200);
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-4">
      <div className="bg-[#171f33] border border-[#4cd7f6]/40 rounded-xl max-w-3xl w-full p-6 shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* 头部 */}
        <div className="flex justify-between items-center pb-4 border-b border-[#444653]/40">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-[#4cd7f6]" />
            <h3 className="text-[18px] font-bold text-[#dae2fd]">工程财务监管与税务风控审计简报生成器</h3>
          </div>
          <button onClick={onClose} className="text-[#8e909f] hover:text-[#dae2fd] cursor-pointer">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 报表预览容器 */}
        <div className="flex-1 overflow-y-auto p-4 my-3 bg-[#0b1326] border border-[#444653]/40 rounded-xl text-[13px] font-mono-num space-y-4 scrollbar-hide">
          <div className="text-center pb-4 border-b border-[#444653]/30">
            <h2 className="text-[20px] font-bold text-[#dae2fd] tracking-wide">
              大型建筑工程投资与涉税合规专项审查报告
            </h2>
            <p className="text-[12px] text-[#8e909f] mt-1">
              报告编制时间：2026年08月17日 · 审计机构：锐宝财税智能合规风控中心
            </p>
          </div>

          {/* 宏观概要 */}
          <div className="grid grid-cols-3 gap-3 p-3 bg-[#131b2e] rounded-lg border border-[#444653]/30">
            <div>
              <span className="text-[11px] text-[#8e909f]">纳入监管工程总数:</span>
              <p className="text-[16px] font-bold text-[#dae2fd]">142 个标段</p>
            </div>
            <div>
              <span className="text-[11px] text-[#8e909f]">已批复总概算:</span>
              <p className="text-[16px] font-bold text-[#b8c4ff]">¥ 84.0 亿元</p>
            </div>
            <div>
              <span className="text-[11px] text-[#8e909f]">涉税高危预警项:</span>
              <p className="text-[16px] font-bold text-[#EF4444]">14 项待闭环</p>
            </div>
          </div>

          {/* 重点工程合规一览 */}
          <div>
            <h4 className="text-[14px] font-bold text-[#dae2fd] mb-2 flex items-center gap-1.5">
              <Building2 className="w-4 h-4 text-[#4cd7f6]" />
              <span>重点工程项目财务与税务穿透总账</span>
            </h4>
            <table className="w-full text-left border-collapse border border-[#444653]/30 text-[12px]">
              <thead className="bg-[#171f33] text-[#8e909f]">
                <tr>
                  <th className="p-2 border border-[#444653]/30">工程编号 / 项目名称</th>
                  <th className="p-2 border border-[#444653]/30 text-right">已批总预算</th>
                  <th className="p-2 border border-[#444653]/30 text-right">累计支出</th>
                  <th className="p-2 border border-[#444653]/30 text-center">资金消耗率</th>
                  <th className="p-2 border border-[#444653]/30 text-center">税务风险评级</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#444653]/30">
                {projects.map((p) => (
                  <tr key={p.id} className="hover:bg-[#1e293b]/40">
                    <td className="p-2 border border-[#444653]/30 font-bold text-[#dae2fd]">{p.projectCode} · {p.name}</td>
                    <td className="p-2 border border-[#444653]/30 text-right">¥ {(p.totalBudget / 100000000).toFixed(2)} 亿</td>
                    <td className="p-2 border border-[#444653]/30 text-right text-[#b8c4ff]">¥ {(p.spentAmount / 100000000).toFixed(2)} 亿</td>
                    <td className="p-2 border border-[#444653]/30 text-center">{p.progressPercent}%</td>
                    <td className="p-2 border border-[#444653]/30 text-center font-bold text-[#4cd7f6]">{p.taxRiskGrade}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 专家意见 */}
          <div className="p-3.5 bg-[#171f33] rounded-lg border border-[#4cd7f6]/30 text-[12px] leading-relaxed text-[#dae2fd]">
            <p className="font-bold text-[#4cd7f6] mb-1">审计审查综合意见：</p>
            <p>1. 总体资金池周转稳健，综合毛利率维持在 27.3% 的行业优良水平；</p>
            <p>2. 建议针对【成都天府国际金融中心二期大厦·建筑劳务分包】启动异地施工跨区预缴与个税核销复核，规避漏缴补税风险；</p>
            <p>3. 严格落实【发票四流合一】闭环审查，强化大额钢材物资现场过磅入库验收单据完备性。</p>
          </div>
        </div>

        {/* 底部操作 */}
        <div className="flex justify-between items-center pt-3 border-t border-[#444653]/40">
          <div>
            {exportSuccess && (
              <span className="text-[12px] text-[#10B981] flex items-center gap-1 font-bold animate-pulse">
                <CheckCircle className="w-4 h-4" />
                报表数据已成功生成并下载
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-[#2d3449] hover:bg-[#31394d] text-[#dae2fd] text-[13px] font-semibold rounded-lg cursor-pointer"
            >
              关闭
            </button>
            <button
              onClick={handlePrint}
              className="px-4 py-2 bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] text-[13px] font-semibold rounded-lg border border-[#4cd7f6]/30 flex items-center gap-1.5 cursor-pointer"
            >
              <Printer className="w-4 h-4 text-[#4cd7f6]" />
              <span>打印归档</span>
            </button>
            <button
              onClick={handleDownload}
              disabled={isExporting}
              className="px-5 py-2 bg-[#03b5d3] hover:bg-[#03b5d3]/80 text-[#001f26] text-[13px] font-bold rounded-lg flex items-center gap-1.5 cursor-pointer shadow-[0_0_12px_rgba(76,215,246,0.3)] disabled:opacity-50"
            >
              <Download className="w-4 h-4" />
              <span>{isExporting ? '生成报表中...' : '下载电子表格 (Excel/PDF)'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
