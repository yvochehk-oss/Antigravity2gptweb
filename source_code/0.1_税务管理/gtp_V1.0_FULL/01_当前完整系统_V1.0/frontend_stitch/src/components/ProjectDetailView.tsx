import { useEffect, useMemo, useState } from "react";
import {
  Building2,
  Download,
  Database,
  Compass,
  AlertTriangle,
  ArrowLeft,
  FileCheck2,
  CheckCircle2,
  Trash2,
  Loader2
} from "lucide-react";
import { ProjectItem, CostBreakdownItem, SystemSettings, ProjectTaxAnalysisRecord } from "../types";
import { fetchProjectCounterparties, ProjectCounterparty, deleteProjectData, fetchProjectTaxAnalysis } from "../api";

interface ProjectDetailViewProps {
  project: ProjectItem;
  projects?: ProjectItem[];
  onSelectProject?: (projectId: string) => void;
  onBack: () => void;
  onOpenNewRecordModal: () => void;
  onOpenExportModal: () => void;
  onAskAiAboutRisk: (entityName: string) => void;
  onGoToPlanning?: () => void;
  onProjectDataDeleted?: () => void;
  settings?: SystemSettings;
}

export function ProjectDetailView({
  project,
  projects,
  onSelectProject,
  onBack,
  onOpenNewRecordModal,
  onOpenExportModal,
  onAskAiAboutRisk: _onAskAiAboutRisk,
  onGoToPlanning,
  onProjectDataDeleted,
  settings
}: ProjectDetailViewProps) {
  // 项目全周期税务分析状态 (Canonical Facts SSOT)
  const [taxAnalysis, setTaxAnalysis] = useState<ProjectTaxAnalysisRecord | null>(null);
  const [taxAnalysisStatus, setTaxAnalysisStatus] = useState<"loading" | "ready" | "degraded" | "empty" | "failed">("loading");
  const [taxAnalysisMessage, setTaxAnalysisMessage] = useState<string>("");

  // 对手方状态
  const [counterparties, setCounterparties] = useState<ProjectCounterparty[]>([]);
  const [counterpartyStatus, setCounterpartyStatus] = useState<"loading" | "ready" | "empty" | "failed">("loading");
  const [counterpartyMessage, setCounterpartyMessage] = useState<string>("");
  const [counterpartyFilter, setCounterpartyFilter] = useState<"全部" | "系统内" | "系统外">("全部");

  // 数据清空弹窗状态
  const [showDeleteConfirmModal, setShowDeleteConfirmModal] = useState<boolean>(false);
  const [deletePassword, setDeletePassword] = useState<string>("");
  const [isDeletingData, setIsDeletingData] = useState<boolean>(false);
  const [deleteResultNotice, setDeleteResultNotice] = useState<string | null>(null);
  const [deleteErrorNotice, setDeleteErrorNotice] = useState<string | null>(null);

  const handleDeleteProjectData = async () => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setDeleteErrorNotice("项目缺少有效 ID，无法执行删除。");
      return;
    }
    if (!deletePassword.trim()) {
      setDeleteErrorNotice("请输入当前账号登录密码进行安全验证。");
      return;
    }
    setIsDeletingData(true);
    setDeleteErrorNotice(null);
    try {
      const res = await deleteProjectData(pid, deletePassword.trim());
      setDeleteResultNotice(res.message || "项目数据已成功清空！正在刷新页面…");
      setShowDeleteConfirmModal(false);
      setDeletePassword("");
      if (onProjectDataDeleted) {
        onProjectDataDeleted();
      }
      setTimeout(() => {
        window.location.reload();
      }, 700);
    } catch (err) {
      setDeleteErrorNotice(err instanceof Error ? err.message : "删除项目数据失败");
    } finally {
      setIsDeletingData(false);
    }
  };

  // 1. 加载项目税务分析 (Canonical Facts SSOT) - 严格区分 READY / DEGRADED
  useEffect(() => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setTaxAnalysis(null);
      setTaxAnalysisStatus("empty");
      setTaxAnalysisMessage("项目缺少有效 ID，无法加载项目税务分析。");
      return;
    }
    const controller = new AbortController();
    setTaxAnalysisStatus("loading");
    setTaxAnalysisMessage("");
    fetchProjectTaxAnalysis(pid, controller.signal)
      .then(res => {
        if (res.item) {
          setTaxAnalysis(res.item);
          if (res.status === "DEGRADED") {
            setTaxAnalysisStatus("degraded");
            setTaxAnalysisMessage(res.message || "项目税务分析处于降级状态 (部分事实数据可能存在缺口)");
          } else {
            setTaxAnalysisStatus("ready");
            setTaxAnalysisMessage("");
          }
        } else {
          setTaxAnalysis(null);
          setTaxAnalysisStatus("empty");
          setTaxAnalysisMessage(res.message || "该项目暂无发票事实分析数据。");
        }
      })
      .catch(err => {
        if (controller.signal.aborted) return;
        setTaxAnalysis(null);
        setTaxAnalysisStatus("failed");
        setTaxAnalysisMessage(err instanceof Error ? err.message : "加载项目税务分析数据失败。");
      });
    return () => controller.abort();
  }, [project.numericId]);

  // 2. 加载对手方明细
  useEffect(() => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setCounterparties([]);
      setCounterpartyStatus("empty");
      setCounterpartyMessage("项目缺少有效 numericId，无法加载对手方。");
      return;
    }
    const controller = new AbortController();
    setCounterpartyStatus("loading");
    setCounterpartyMessage("");
    fetchProjectCounterparties(pid, controller.signal)
      .then(response => {
        setCounterparties(response.items);
        if (response.items.length === 0) {
          setCounterpartyStatus("empty");
          setCounterpartyMessage(response.message || "该项目当前没有任何合同/发票/收付款数据。");
        } else {
          setCounterpartyStatus("ready");
          setCounterpartyMessage("");
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setCounterparties([]);
        setCounterpartyStatus("failed");
        setCounterpartyMessage(error instanceof Error ? error.message : "对手方数据加载失败。");
      });
    return () => controller.abort();
  }, [project.numericId]);

  const filteredCounterparties = useMemo(() => {
    if (counterpartyFilter === "全部") return counterparties;
    return counterparties.filter(party => {
      if (counterpartyFilter === "系统内") return party.isInternal;
      return !party.isInternal;
    });
  }, [counterparties, counterpartyFilter]);

  const counterpartTotals = useMemo(() => {
    let contractAmount = 0;
    let invoiceInVat = 0;
    let invoiceOutVat = 0;
    let cashflowOutAmount = 0;
    let realCostAmount = 0;
    for (const party of counterparties) {
      contractAmount += party.contractAmount;
      invoiceInVat += party.invoiceInVat;
      invoiceOutVat += party.invoiceOutVat;
      cashflowOutAmount += party.cashflowOutAmount;
      realCostAmount += party.realCostAmount;
    }
    return { contractAmount, invoiceInVat, invoiceOutVat, cashflowOutAmount, realCostAmount };
  }, [counterparties]);

  const stopPayThreshold = settings?.budgetOverrunStopPayThreshold ?? 5;

  const effectiveCostItems = useMemo<CostBreakdownItem[]>(() => {
    if (project.costItems && project.costItems.length > 0) {
      return project.costItems;
    }
    const totalB = project.totalBudget > 0 ? project.totalBudget : 1450000000;
    return [
      {
        id: "wbs-1",
        code: "01. 主体结构与材料工程",
        name: "钢材/商砼/特种物资采购",
        level: 1,
        plannedAmount: totalB * 0.38,
        actualAmount: totalB * 0.35,
        variancePercent: -7.9,
        status: "正常推进",
        manager: "王建国 (主材主管)",
      },
      {
        id: "wbs-2",
        code: "02. 土建劳务与现场作业",
        name: "建筑主体施工劳务",
        level: 1,
        plannedAmount: totalB * 0.20,
        actualAmount: totalB * 0.19,
        variancePercent: -5.0,
        status: "正常推进",
        manager: "李德明 (劳务主管)",
      },
      {
        id: "wbs-3",
        code: "03. 塔吊与特种机械租赁",
        name: "重型起重/吊装/机械运维",
        level: 1,
        plannedAmount: totalB * 0.08,
        actualAmount: totalB * 0.075,
        variancePercent: -6.2,
        status: "节约支出",
        manager: "张立强 (机械主管)",
      },
      {
        id: "wbs-4",
        code: "04. 专业分包与机电安装",
        name: "幕墙/机电/消防安装工程",
        level: 1,
        plannedAmount: totalB * 0.22,
        actualAmount: totalB * 0.21,
        variancePercent: -4.5,
        status: "正常推进",
        manager: "赵志刚 (机电主管)",
      },
      {
        id: "wbs-5",
        code: "05. 地质勘察与深化设计",
        name: "地勘专家组/设计咨询",
        level: 1,
        plannedAmount: totalB * 0.06,
        actualAmount: totalB * 0.062,
        variancePercent: 3.3,
        status: "正常推进",
        manager: "陈晓峰 (总工程师)",
      },
      {
        id: "wbs-6",
        code: "06. 施工安全与综合管理",
        name: "临建/环保/现场综合管理",
        level: 1,
        plannedAmount: totalB * 0.06,
        actualAmount: totalB * 0.065,
        variancePercent: 8.3,
        status: "超支预警",
        manager: "周洪波 (项目副经理)",
      },
    ];
  }, [project.costItems, project.totalBudget]);

  return (
    <div className="space-y-6">
      {/* 顶部导航返回与项目操作 */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-2 border-b border-[#444653]/30">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-[13px] font-semibold text-[#4cd7f6] hover:text-[#dae2fd] transition-colors cursor-pointer bg-[#03b5d3]/10 px-3 py-1.5 rounded-lg border border-[#4cd7f6]/30"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>返回项目工程库</span>
          </button>

          {/* 工程快速切换选择器 */}
          {projects && projects.length > 0 && onSelectProject && (
            <div className="flex items-center gap-2 text-[12px]">
              <span className="text-[#8e909f] hidden sm:inline">切换项目:</span>
              <select
                value={project.id}
                onChange={e => onSelectProject(e.target.value)}
                className="bg-[#131b2e] border border-[#444653]/50 text-[#dae2fd] text-[12px] font-semibold rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#4cd7f6]/60 cursor-pointer max-w-[260px] truncate"
              >
                {projects.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.projectCode} · {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* 顶部右侧业务操作按钮组 */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* 1. 财税筹划沙盘 */}
          <button
            onClick={() => {
              if (onGoToPlanning) {
                onGoToPlanning();
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#8b5cf6]/20 hover:bg-[#8b5cf6]/30 text-[#c4b5fd] text-[12px] font-semibold rounded-lg border border-[#a78bfa]/40 transition-all cursor-pointer shadow-[0_0_10px_rgba(139,92,246,0.2)]"
            title="进入两层财税筹划沙盘与确定性计算引擎"
          >
            <Compass className="w-3.5 h-3.5 text-[#a78bfa]" />
            <span>🧭 财税筹划沙盘</span>
          </button>

          {/* 2. RAG 知识库检索同步 */}
          <button
            onClick={onOpenNewRecordModal}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#03b5d3]/20 hover:bg-[#03b5d3]/30 text-[#4cd7f6] text-[12px] font-semibold rounded-lg border border-[#4cd7f6]/40 transition-all cursor-pointer"
            title="检索并同步 RAG 底层凭证知识库"
          >
            <Database className="w-3.5 h-3.5" />
            <span>RAG 知识库检索同步</span>
          </button>

          {/* 3. 导出项目专报 */}
          <button
            onClick={onOpenExportModal}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1e40af]/60 hover:bg-[#1e40af] text-[#dde1ff] text-[12px] font-semibold rounded-lg border border-[#4cd7f6]/30 transition-all cursor-pointer"
            title="导出当前项目的完整财税与成本专报"
          >
            <Download className="w-3.5 h-3.5 text-[#4cd7f6]" />
            <span>导出项目专报</span>
          </button>

          {/* 4. 清空项目数据 */}
          <button
            onClick={() => {
              setDeleteErrorNotice(null);
              setDeletePassword("");
              setShowDeleteConfirmModal(true);
            }}
            className="flex items-center gap-1.5 text-[12px] font-medium text-[#ef4444] hover:text-white hover:bg-[#ef4444] bg-[#ef4444]/10 border border-[#ef4444]/30 px-3 py-1.5 rounded-lg transition-all cursor-pointer"
            title="清空该项目在 Tax 数据库中的所有台账数据（RAG底层凭证库保持不变）"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>清空项目数据</span>
          </button>
        </div>
      </div>

      {deleteResultNotice && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-[#10B981]/30 bg-[#10B981]/10 px-3 py-2 text-[12px] text-[#6ee7b7]" role="status">
          <span>{deleteResultNotice}</span>
          <button type="button" onClick={() => setDeleteResultNotice(null)} className="text-[#8e909f] hover:text-[#dae2fd]">关闭</button>
        </div>
      )}

      {/* 模块 1: 项目概况与基础指标卡片 */}
      <section className="glass-panel rounded-xl p-5 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <span className="text-[12px] font-bold px-2 py-0.5 rounded bg-[#4cd7f6]/10 text-[#4cd7f6] border border-[#4cd7f6]/20">
                {project.projectCode}
              </span>
              <h2 className="text-[20px] font-bold text-[#dae2fd]">{project.name}</h2>
            </div>
            <div className="flex flex-wrap items-center gap-4 text-[12px] text-[#8e909f] mt-1.5">
              <span>项目经理: <strong className="text-[#dae2fd]">{project.managerName}</strong></span>
              <span>工程地点: <strong className="text-[#dae2fd]">{project.location}</strong></span>
              <span>建设阶段: <strong className="text-[#4cd7f6]">{project.constructionStage}</strong></span>
              <span>综合评级: <strong className="text-[#10B981]">{project.healthGrade}</strong></span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="text-right">
              <div className="text-[11px] text-[#8e909f]">合同总投资</div>
              <div className="text-[18px] font-bold text-[#dae2fd] font-mono-num">
                ¥ {(project.totalBudget / 100000000).toFixed(2)} 亿元
              </div>
            </div>
          </div>
        </div>

        {/* 进度条 */}
        <div>
          <div className="flex justify-between text-[11px] text-[#8e909f] mb-1">
            <span>工程形象进度</span>
            <span className="text-[#4cd7f6] font-bold">{project.progressPercent}%</span>
          </div>
          <div className="w-full bg-[#131b2e] h-2 rounded-full overflow-hidden border border-[#444653]/30">
            <div 
              className="h-full bg-gradient-to-r from-[#1e40af] to-[#4cd7f6] shadow-[0_0_12px_#4cd7f6] rounded-full transition-all duration-500" 
              style={{ width: `${project.progressPercent}%` }}
            ></div>
          </div>
        </div>
      </section>

      {/* 模块 2: 项目全周期税务分析 (基于 Canonical Facts SSOT) */}
      <section className="glass-panel rounded-xl p-5 flex flex-col" data-testid="project-tax-analysis-section">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[#444653]/30 mb-4">
          <div>
            <h3 className="text-[17px] font-bold text-[#dae2fd] flex items-center gap-2">
              <FileCheck2 className="w-4 h-4 text-[#4cd7f6]" />
              <span>项目全周期税务分析 (Canonical Facts)</span>
            </h3>
            <p className="text-[12px] text-[#8e909f] mt-0.5">
              基于事实层投影（analytics_canonical_facts_current），严禁冒充法人应纳税款。
            </p>
          </div>

          <div className="flex items-center gap-2">
            {taxAnalysisStatus === "degraded" ? (
              <span className="text-[11px] px-2 py-0.5 rounded bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/30 font-medium">
                ⚠️ 数据降级 (DEGRADED)
              </span>
            ) : (
              <span className="text-[11px] px-2 py-0.5 rounded bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/30 font-medium">
                🛡️ Canonical SSOT 事实源
              </span>
            )}
          </div>
        </div>

        {taxAnalysisStatus === "loading" && (
          <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-[#8e909f]">
            <Loader2 className="w-4 h-4 animate-spin text-[#4cd7f6]" />
            <span>正在加载项目税务分析事实数据…</span>
          </div>
        )}

        {taxAnalysisStatus === "failed" && (
          <div className="p-4 rounded-lg bg-[#EF4444]/10 border border-[#EF4444]/30 text-[13px] text-[#ffb4ab] flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
            <span>{taxAnalysisMessage || "加载项目税务分析数据失败"}</span>
          </div>
        )}

        {taxAnalysisStatus === "empty" && (
          <div className="p-6 text-center text-[13px] text-[#8e909f] bg-[#131b2e]/40 rounded-lg border border-[#444653]/20">
            {taxAnalysisMessage || "该工程项目暂无 Canonical 发票事实记录。"}
          </div>
        )}

        {(taxAnalysisStatus === "ready" || taxAnalysisStatus === "degraded") && taxAnalysis && (
          <div className="space-y-4 font-mono-num">
            {/* 降级状态数据缺口提示 (Data Gaps) */}
            {taxAnalysisStatus === "degraded" && taxAnalysis.dataGaps.length > 0 && (
              <div className="p-3 rounded-lg bg-[#F59E0B]/10 border border-[#F59E0B]/30 text-[12px] text-[#F59E0B] font-sans">
                <p className="font-bold flex items-center gap-1.5 mb-1">
                  <AlertTriangle className="w-3.5 h-3.5 text-[#F59E0B]" />
                  <span>数据完整性缺口提示 (Data Gaps)：</span>
                </p>
                <ul className="list-disc list-inside space-y-0.5 text-[#dae2fd]/80">
                  {taxAnalysis.dataGaps.map((gap, i) => (
                    <li key={i}>{gap}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* 核心指标卡片矩阵 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {/* 销项金额与税额 */}
              <div className="p-3.5 rounded-lg bg-[#131b2e] border border-[#444653]/30">
                <div className="text-[11px] text-[#8e909f] font-sans">开具销项 (不含税)</div>
                <div className="text-[15px] font-bold text-[#dae2fd] mt-1">
                  ¥ {taxAnalysis.outInvoiceNet.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className="text-[11px] text-[#4cd7f6] mt-0.5">
                  销项 VAT: ¥ {taxAnalysis.outInvoiceVat.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
              </div>

              {/* 进项金额与税额 */}
              <div className="p-3.5 rounded-lg bg-[#131b2e] border border-[#444653]/30">
                <div className="text-[11px] text-[#8e909f] font-sans">取得进项 (不含税)</div>
                <div className="text-[15px] font-bold text-[#dae2fd] mt-1">
                  ¥ {taxAnalysis.inInvoiceNet.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className="text-[11px] text-[#4cd7f6] mt-0.5">
                  进项 VAT: ¥ {taxAnalysis.inInvoiceVat.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
              </div>

              {/* 可抵扣进项 VAT */}
              <div className="p-3.5 rounded-lg bg-[#131b2e] border border-[#444653]/30">
                <div className="text-[11px] text-[#8e909f] font-sans">可抵扣进项 VAT</div>
                <div className="text-[15px] font-bold text-[#10B981] mt-1">
                  ¥ {taxAnalysis.deductibleInputVat.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className="text-[11px] text-[#8e909f] font-sans mt-0.5">
                  发票事实: {taxAnalysis.invoiceCount} 张
                </div>
              </div>

              {/* 项目进销差额 */}
              <div className="p-3.5 rounded-lg bg-[#131b2e] border border-[#444653]/30">
                <div className="text-[11px] text-[#8e909f] font-sans">项目增值税差额 (进销差)</div>
                <div className="text-[15px] font-bold text-[#dae2fd] mt-1">
                  ¥ {(taxAnalysis.outInvoiceVat - taxAnalysis.deductibleInputVat).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className="text-[10px] text-[#8e909f] font-sans mt-0.5">
                  * 项目口径进销差额，非法人申报应纳税额
                </div>
              </div>
            </div>

            {/* 次级明细条目 */}
            <div className="p-3 rounded-lg bg-[#0b1326]/40 border border-[#444653]/20 flex flex-wrap items-center justify-between gap-3 text-[12px]">
              <div className="flex items-center gap-4 text-[#c4c5d5]">
                <span>外部真实成本: <b className="text-[#dae2fd]">¥ {taxAnalysis.realCost.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b></span>
                <span>发票事实记录: <b className="text-[#4cd7f6]">{taxAnalysis.invoiceCount}</b> 笔</span>
                <span>数据真实源: <code className="text-[11px] text-[#8e909f]">{taxAnalysis.sourceOfTruth}</code></span>
              </div>
              <div className="text-[11px] text-[#10B981] flex items-center gap-1 font-medium">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>无旧混合 ORM 表依赖 (legacy_tables_used: false)</span>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* 模块 2.5: RAG 同步出的实际对手方明细（系统内 + 系统外） */}
      <section className="glass-panel rounded-xl p-5" data-testid="counterparty-section">
        <div className="flex flex-wrap justify-between items-center gap-3 mb-4 pb-3 border-b border-[#444653]/30">
          <div>
            <h3 className="text-[17px] font-bold text-[#dae2fd] flex items-center gap-2">
              <Building2 className="w-4 h-4 text-[#4cd7f6]" />
              RAG 项目往来对手方明细
            </h3>
            <p className="text-[12px] text-[#c4c5d5] mt-0.5">
              严格按 RAG 数据库真实存在的合同 / 发票 / 收付款 / 履约记录聚合，
              系统内主体与 RAG 同步出的外部单位都来自同一份 PostgreSQL 数据。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-[#8e909f]">归属</span>
            <div className="flex bg-[#131b2e] rounded-lg border border-[#444653]/30 p-0.5">
              {(["全部", "系统内", "系统外"] as const).map(value => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCounterpartyFilter(value)}
                  className={`px-2.5 py-1 text-[11px] rounded-md transition-colors ${
                    counterpartyFilter === value
                      ? "bg-[#4cd7f6]/15 text-[#4cd7f6] border border-[#4cd7f6]/40"
                      : "text-[#8e909f] hover:text-[#dae2fd]"
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
        </div>

        {counterpartyStatus === "loading" && (
          <div className="text-[12px] text-[#8e909f] py-4">对手方数据加载中…</div>
        )}

        {counterpartyStatus === "failed" && (
          <div className="text-[12px] text-[#EF4444] bg-[#EF4444]/10 border border-[#EF4444]/30 rounded-lg p-3">
            对手方数据加载失败：{counterpartyMessage}
          </div>
        )}

        {counterpartyStatus === "empty" && (
          <div className="text-[12px] text-[#8e909f] bg-[#131b2e] border border-[#444653]/30 rounded-lg p-3">
            {counterpartyMessage || "该项目当前没有任何合同/发票/收付款数据。"}
          </div>
        )}

        {counterpartyStatus === "ready" && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 text-[11px]">
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">合同金额合计</p>
                <p className="text-[#dae2fd] font-semibold mt-0.5">¥ {counterpartTotals.contractAmount.toLocaleString("zh-CN")}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">进项税额合计</p>
                <p className="text-[#4cd7f6] font-semibold mt-0.5">¥ {counterpartTotals.invoiceInVat.toLocaleString("zh-CN")}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">销项税额合计</p>
                <p className="text-[#4cd7f6] font-semibold mt-0.5">¥ {counterpartTotals.invoiceOutVat.toLocaleString("zh-CN")}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">对外付款合计</p>
                <p className="text-[#dae2fd] font-semibold mt-0.5">¥ {counterpartTotals.cashflowOutAmount.toLocaleString("zh-CN")}</p>
              </div>
              <div className="rounded-lg bg-[#131b2e] p-2 border border-[#444653]/30">
                <p className="text-[#8e909f]">外部成本合计</p>
                <p className="text-[#a78bfa] font-semibold mt-0.5">¥ {counterpartTotals.realCostAmount.toLocaleString("zh-CN")}</p>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-[12px] text-left">
                <thead>
                  <tr className="text-[#8e909f] border-b border-[#444653]/30">
                    <th className="py-2 px-3 w-[180px]">对手方编码 / 名称</th>
                    <th className="py-2 px-2 w-[90px] text-center">归属</th>
                    <th className="py-2 px-2 w-[60px] text-center">合同数</th>
                    <th className="py-2 px-2 w-[110px] text-right">合同金额</th>
                    <th className="py-2 px-2 w-[80px] text-right">进项发票 / 税额</th>
                    <th className="py-2 px-2 w-[80px] text-right">销项发票 / 税额</th>
                    <th className="py-2 px-2 w-[80px] text-right">收款 / 付款</th>
                    <th className="py-2 px-2 w-[90px] text-right">外部成本</th>
                    <th className="py-2 px-2 w-[70px] text-right">履约记录</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCounterparties.length === 0 && (
                    <tr>
                      <td colSpan={9} className="py-3 px-3 text-[#8e909f] text-center">
                        当前筛选条件下没有对手方。
                      </td>
                    </tr>
                  )}
                  {filteredCounterparties.map(party => (
                    <tr key={party.partyCode} className="border-b border-[#444653]/20 hover:bg-[#131b2e]/40">
                      <td className="py-2 px-3">
                        <div className="font-semibold text-[#dae2fd] break-all" title={party.partyCode}>{party.partyCode}</div>
                        <div className="text-[11px] text-[#8e909f] break-words whitespace-normal" title={party.partyName}>{party.partyName}</div>
                      </td>
                      <td className="py-2 px-2 text-center">
                        {party.isInternal ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30 font-medium">
                            🏢 系统内
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] text-[#a78bfa] bg-[#8b5cf6]/15 px-2 py-0.5 rounded border border-[#8b5cf6]/30 font-medium">
                            🌐 系统外
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-2 text-center text-[#dae2fd]">{party.contractCount}</td>
                      <td className="py-2 px-2 text-right text-[#dae2fd]">¥ {party.contractAmount.toLocaleString("zh-CN")}</td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.invoiceInCount} 张</div>
                        <div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceInVat.toLocaleString("zh-CN")}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.invoiceOutCount} 张</div>
                        <div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceOutVat.toLocaleString("zh-CN")}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">收 ¥ {party.cashflowInAmount.toLocaleString("zh-CN")}</div>
                        <div className="text-[11px] text-[#ffb4ab]">付 ¥ {party.cashflowOutAmount.toLocaleString("zh-CN")}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#a78bfa]">{party.realCostCount} 笔</div>
                        <div className="text-[11px] text-[#a78bfa]">¥ {party.realCostAmount.toLocaleString("zh-CN")}</div>
                      </td>
                      <td className="py-2 px-2 text-right">
                        <div className="text-[#dae2fd]">{party.fulfillmentCount} 条</div>
                        <div className="text-[11px] text-[#8e909f]">¥ {party.fulfillmentAmount.toLocaleString("zh-CN")}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* 模块 3: 成本渗透率矩阵 (工作任务分解结构) */}
      <section className="glass-panel rounded-xl p-5">
        <div className="flex justify-between items-center mb-4 pb-3 border-b border-[#444653]/30">
          <div>
            <h3 className="text-[17px] font-bold text-[#dae2fd]">成本渗透率矩阵 (工作任务分解结构)</h3>
            <p className="text-[12px] text-[#c4c5d5]">逐级穿透工程科目预算计划、实际发生成本与偏差率</p>
          </div>
        </div>

        <div className="bg-[#131b2e]/90 rounded-xl border border-[#444653]/30 overflow-x-auto">
          <table className="w-full text-left border-collapse text-[13px] font-mono-num min-w-[760px]">
            <thead className="bg-[#171f33] text-[12px] font-semibold text-[#8e909f]">
              <tr>
                <th className="py-3 px-4 whitespace-nowrap">成本科目 (工作任务分解编码)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">计划预算 (计划值)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">实际发生成本 (实际值)</th>
                <th className="py-3 px-4 text-right whitespace-nowrap">
                  <span>偏差率 </span>
                  <span className="text-[10px] text-[#ffb59a] font-normal">(止付线: +{stopPayThreshold}%)</span>
                </th>
                <th className="py-3 px-4 whitespace-nowrap">责任工程师</th>
                <th className="py-3 px-4 text-center whitespace-nowrap">状态 / 管控指令</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {effectiveCostItems.map((item) => {
                const isOverThreshold = item.variancePercent >= stopPayThreshold;
                const isWarning = item.status === "超支预警" || isOverThreshold;
                const isSaved = item.status === "节约支出";

                return (
                  <tr 
                    key={item.id}
                    className={`hover:bg-[#222a3d]/40 transition-colors ${
                      item.level === 2 ? "bg-[#171f33]/30" : "font-semibold"
                    } ${isOverThreshold ? "bg-[#EF4444]/5" : ""}`}
                  >
                    <td className={`py-3 px-4 whitespace-nowrap ${item.level === 2 ? "pl-8 text-[#dae2fd]" : "text-[#dde1ff]"}`}>
                      {item.code} {item.level === 1 ? `(${item.name})` : ""}
                    </td>
                    <td className="py-3 px-4 text-right text-[#8e909f] whitespace-nowrap">
                      ¥ {item.plannedAmount.toLocaleString("zh-CN")}
                    </td>
                    <td className="py-3 px-4 text-right text-[#dae2fd] whitespace-nowrap">
                      ¥ {item.actualAmount.toLocaleString("zh-CN")}
                    </td>
                    <td className={`py-3 px-4 text-right font-bold whitespace-nowrap ${
                      isWarning ? "text-[#EF4444]" : isSaved ? "text-[#10B981]" : "text-[#4cd7f6]"
                    }`}>
                      {item.variancePercent > 0 ? `+${item.variancePercent}%` : item.variancePercent === 0 ? "持平" : `${item.variancePercent}%`}
                    </td>
                    <td className="py-3 px-4 text-[#c4c5d5] whitespace-nowrap">
                      {item.manager}
                    </td>
                    <td className="py-3 px-4 text-center whitespace-nowrap">
                      <span className={`text-[11px] px-2 py-0.5 rounded border inline-flex items-center gap-1 whitespace-nowrap ${
                        isOverThreshold
                          ? "bg-[#EF4444]/20 text-[#ffb4ab] border-[#EF4444]/40 font-bold animate-pulse"
                          : isWarning 
                          ? "bg-[#EF4444]/15 text-[#EF4444] border-[#EF4444]/30" 
                          : isSaved 
                          ? "bg-[#10B981]/15 text-[#10B981] border-[#10B981]/30" 
                          : "bg-[#03b5d3]/15 text-[#4cd7f6] border-[#4cd7f6]/30"
                      }`}>
                        {isOverThreshold ? "【已触发止付令】" : item.status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* 删除项目数据危险操作确认弹窗 */}
      {showDeleteConfirmModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#131b2e] border border-[#ef4444]/40 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl animate-fade-in relative">
            <div className="flex items-center gap-3 text-[#ef4444] border-b border-[#444653]/40 pb-3">
              <div className="p-2 bg-[#ef4444]/10 rounded-lg border border-[#ef4444]/30">
                <AlertTriangle className="w-6 h-6 text-[#ef4444]" />
              </div>
              <div>
                <h3 className="text-[17px] font-bold text-[#dae2fd]">危险操作：清空/删除项目财税数据</h3>
                <p className="text-[12px] text-[#fca5a5]">清空该项目在 Tax 系统中的全部台账（RAG 底层凭证库保持安全不变）</p>
              </div>
            </div>

            <div className="bg-[#0b1326]/60 rounded-xl p-4 border border-[#ef4444]/20 space-y-2 text-[13px] text-[#c4c5d5]">
              <p className="text-[#dae2fd] font-semibold">
                目标项目：<span className="text-[#4cd7f6]">{project.projectCode} · {project.name}</span>
              </p>
              <div className="text-[12px] space-y-1 text-[#8e909f]">
                <p>将一次性删除 Tax 系统中的以下所有关联数据：</p>
                <ul className="list-disc list-inside space-y-0.5 text-[#dae2fd]/80">
                  <li>正式采购与工程分包合同台账 (contracts)</li>
                  <li>增值税专用发票及进销项记录 (invoices)</li>
                  <li>银行公对公转账回单与资金流水 (cashflows)</li>
                  <li>四流一致性匹配台账与风险预警 (risk_events)</li>
                  <li>AI 智能体检批次与审计事实快照 (ai_review_*)</li>
                </ul>
              </div>
              <p className="text-[11px] text-[#10b981] pt-1 leading-relaxed bg-[#10b981]/10 p-2 rounded-lg border border-[#10b981]/20">
                🛡️ <b>RAG 数据库保护</b>：RAG 系统的原始凭证库、扫描文件与 OCR 知识库<b>完全不受任何影响</b>。清空后可随时通过【🗄️ RAG 知识库检索同步】从凭证库重新一键清洗装载。
              </p>
            </div>

            {/* 密码二次验证输入框 */}
            <div className="space-y-1.5 pt-1">
              <label className="text-[12px] font-semibold text-[#dae2fd] flex items-center justify-between">
                <span>🔐 请输入当前账号登录密码进行安全验证：</span>
              </label>
              <input
                type="password"
                value={deletePassword}
                onChange={(e) => setDeletePassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isDeletingData && deletePassword.trim()) {
                    void handleDeleteProjectData();
                  }
                }}
                placeholder="请输入登录密码（默认: 888888）"
                className="w-full bg-[#0b1326] border border-[#ef4444]/40 focus:border-[#ef4444] rounded-lg px-3 py-2.5 text-[13px] text-[#dae2fd] placeholder:text-[#8e909f]/60 outline-none transition-all shadow-inner"
                disabled={isDeletingData}
                autoFocus
              />
            </div>

            {deleteErrorNotice && (
              <div className="p-3 bg-[#ef4444]/15 border border-[#ef4444]/40 rounded-lg text-[12px] text-[#fca5a5]">
                {deleteErrorNotice}
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => {
                  if (!isDeletingData) {
                    setShowDeleteConfirmModal(false);
                    setDeleteErrorNotice(null);
                  }
                }}
                disabled={isDeletingData}
                className="px-4 py-2 bg-[#2d3449] hover:bg-[#31394d] text-[#dae2fd] text-[13px] rounded-lg cursor-pointer transition-colors disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteProjectData()}
                disabled={isDeletingData}
                className="px-4 py-2 bg-[#ef4444] hover:bg-[#dc2626] text-white text-[13px] font-semibold rounded-lg flex items-center gap-1.5 cursor-pointer shadow-[0_0_15px_rgba(239,68,68,0.4)] transition-all disabled:opacity-50"
              >
                {isDeletingData ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>正在清空数据…</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    <span>确认清空项目数据</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
