import { useEffect, useMemo, useState } from 'react';
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
  Loader2,
} from 'lucide-react';
import { ProjectItem, CostBreakdownItem, SystemSettings, ProjectTaxAnalysisRecord } from '../types';
import { fetchProjectCounterparties, ProjectCounterparty, deleteProjectData, fetchProjectTaxAnalysis } from '../api';

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

function dataGapLabel(gap: string): string {
  switch (gap) {
    case 'INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW': return '进项税额合规性待确认';
    case 'INPUT_VAT_ACCOUNTING_IDENTITY_FAILED': return '进项税额勾稽平衡校验异常';
    case 'CANONICAL_INVOICE_PERIOD_MISSING': return '规范发票事实缺少有效期间';
    default: return '存在尚未完成中文归类的数据缺口，请复核后端明细。';
  }
}

export function ProjectDetailView({
  project,
  projects,
  onSelectProject,
  onBack,
  onOpenNewRecordModal,
  onOpenExportModal,
  onGoToPlanning,
  onProjectDataDeleted,
  settings,
}: ProjectDetailViewProps) {
  const [taxAnalysis, setTaxAnalysis] = useState<ProjectTaxAnalysisRecord | null>(null);
  const [taxAnalysisStatus, setTaxAnalysisStatus] = useState<'loading' | 'ready' | 'degraded' | 'empty' | 'failed'>('loading');
  const [taxAnalysisMessage, setTaxAnalysisMessage] = useState<string>('');

  const [counterparties, setCounterparties] = useState<ProjectCounterparty[]>([]);
  const [counterpartyStatus, setCounterpartyStatus] = useState<'loading' | 'ready' | 'empty' | 'failed'>('loading');
  const [counterpartyMessage, setCounterpartyMessage] = useState<string>('');
  const [counterpartyFilter, setCounterpartyFilter] = useState<'全部' | '系统内' | '系统外'>('全部');

  const [showDeleteConfirmModal, setShowDeleteConfirmModal] = useState<boolean>(false);
  const [deletePassword, setDeletePassword] = useState<string>('');
  const [isDeletingData, setIsDeletingData] = useState<boolean>(false);
  const [deleteResultNotice, setDeleteResultNotice] = useState<string | null>(null);
  const [deleteErrorNotice, setDeleteErrorNotice] = useState<string | null>(null);

  const handleDeleteProjectData = async () => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setDeleteErrorNotice('项目缺少有效编号，无法执行删除。');
      return;
    }
    if (!deletePassword.trim()) {
      setDeleteErrorNotice('请输入当前账号登录密码进行安全验证。');
      return;
    }
    setIsDeletingData(true);
    setDeleteErrorNotice(null);
    try {
      const res = await deleteProjectData(pid, deletePassword.trim());
      setDeleteResultNotice(res.message || '项目数据已成功清空！正在刷新页面…');
      setShowDeleteConfirmModal(false);
      setDeletePassword('');
      onProjectDataDeleted?.();
      setTimeout(() => window.location.reload(), 700);
    } catch (err) {
      setDeleteErrorNotice(err instanceof Error ? err.message : '删除项目数据失败');
    } finally {
      setIsDeletingData(false);
    }
  };

  useEffect(() => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setTaxAnalysis(null);
      setTaxAnalysisStatus('empty');
      setTaxAnalysisMessage('项目缺少有效编号，无法加载项目税务分析。');
      return;
    }
    const controller = new AbortController();
    setTaxAnalysisStatus('loading');
    setTaxAnalysisMessage('');
    fetchProjectTaxAnalysis(pid, controller.signal)
      .then(res => {
        if (res.item) {
          setTaxAnalysis(res.item);
          if (res.status === 'DEGRADED') {
            setTaxAnalysisStatus('degraded');
            setTaxAnalysisMessage(res.message || '项目税务分析处于降级状态，部分事实数据可能存在缺口。');
          } else {
            setTaxAnalysisStatus('ready');
            setTaxAnalysisMessage('');
          }
        } else {
          setTaxAnalysis(null);
          setTaxAnalysisStatus('empty');
          setTaxAnalysisMessage(res.message || '该项目暂无发票事实分析数据。');
        }
      })
      .catch(err => {
        if (controller.signal.aborted) return;
        setTaxAnalysis(null);
        setTaxAnalysisStatus('failed');
        setTaxAnalysisMessage(err instanceof Error ? err.message : '加载项目税务分析数据失败。');
      });
    return () => controller.abort();
  }, [project.numericId]);

  useEffect(() => {
    const pid = project.numericId;
    if (!Number.isInteger(pid) || pid <= 0) {
      setCounterparties([]);
      setCounterpartyStatus('empty');
      setCounterpartyMessage('项目缺少有效编号，无法加载对手方。');
      return;
    }
    const controller = new AbortController();
    setCounterpartyStatus('loading');
    setCounterpartyMessage('');
    fetchProjectCounterparties(pid, controller.signal)
      .then(response => {
        setCounterparties(response.items);
        if (response.items.length === 0) {
          setCounterpartyStatus('empty');
          setCounterpartyMessage(response.message || '该项目当前没有任何合同、发票、收付款数据。');
        } else {
          setCounterpartyStatus('ready');
          setCounterpartyMessage('');
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setCounterparties([]);
        setCounterpartyStatus('failed');
        setCounterpartyMessage(error instanceof Error ? error.message : '对手方数据加载失败。');
      });
    return () => controller.abort();
  }, [project.numericId]);

  const filteredCounterparties = useMemo(() => {
    if (counterpartyFilter === '全部') return counterparties;
    return counterparties.filter(party => counterpartyFilter === '系统内' ? party.isInternal : !party.isInternal);
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
  const effectiveCostItems = useMemo<CostBreakdownItem[]>(() => project.costItems?.length ? project.costItems : [], [project.costItems]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#444653]/30 pb-2">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="flex cursor-pointer items-center gap-2 rounded-lg border border-[#4cd7f6]/30 bg-[#03b5d3]/10 px-3 py-1.5 text-[13px] font-semibold text-[#4cd7f6] transition-colors hover:text-[#dae2fd]"><ArrowLeft className="h-4 w-4" /><span>返回项目工程库</span></button>
          {projects && projects.length > 0 && onSelectProject && <div className="flex items-center gap-2 text-[12px]"><span className="hidden text-[#8e909f] sm:inline">切换项目：</span><select value={project.id} onChange={e => onSelectProject(e.target.value)} className="max-w-[260px] cursor-pointer truncate rounded-lg border border-[#444653]/50 bg-[#131b2e] px-2.5 py-1.5 text-[12px] font-semibold text-[#dae2fd] focus:border-[#4cd7f6]/60 focus:outline-none">{projects.map(p => <option key={p.id} value={p.id}>{p.projectCode} · {p.name}</option>)}</select></div>}
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button onClick={() => onGoToPlanning?.()} className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#a78bfa]/40 bg-[#8b5cf6]/20 px-3 py-1.5 text-[12px] font-semibold text-[#c4b5fd] shadow-[0_0_10px_rgba(139,92,246,0.2)] transition-all hover:bg-[#8b5cf6]/30" title="进入财税筹划沙盘与确定性计算引擎"><Compass className="h-3.5 w-3.5 text-[#a78bfa]" /><span>🧭 财税筹划沙盘</span></button>
          <button onClick={onOpenNewRecordModal} className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#4cd7f6]/40 bg-[#03b5d3]/20 px-3 py-1.5 text-[12px] font-semibold text-[#4cd7f6] transition-all hover:bg-[#03b5d3]/30" title="检索并同步底层凭证知识库"><Database className="h-3.5 w-3.5" /><span>凭证知识库检索同步</span></button>
          <button onClick={onOpenExportModal} className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#4cd7f6]/30 bg-[#1e40af]/60 px-3 py-1.5 text-[12px] font-semibold text-[#dde1ff] transition-all hover:bg-[#1e40af]" title="导出当前项目的完整财税与成本专报"><Download className="h-3.5 w-3.5 text-[#4cd7f6]" /><span>导出项目专报</span></button>
          <button onClick={() => { setDeleteErrorNotice(null); setDeletePassword(''); setShowDeleteConfirmModal(true); }} className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#ef4444]/30 bg-[#ef4444]/10 px-3 py-1.5 text-[12px] font-medium text-[#ef4444] transition-all hover:bg-[#ef4444] hover:text-white" title="清空该项目在税务数据库中的全部台账数据（底层凭证知识库保持不变）"><Trash2 className="h-3.5 w-3.5" /><span>清空项目数据</span></button>
        </div>
      </div>

      {deleteResultNotice && <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-[#10B981]/30 bg-[#10B981]/10 px-3 py-2 text-[12px] text-[#6ee7b7]" role="status"><span>{deleteResultNotice}</span><button type="button" onClick={() => setDeleteResultNotice(null)} className="text-[#8e909f] hover:text-[#dae2fd]">关闭</button></div>}

      <section className="glass-panel space-y-4 rounded-xl p-5">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div><div className="flex items-center gap-3"><span className="rounded border border-[#4cd7f6]/20 bg-[#4cd7f6]/10 px-2 py-0.5 text-[12px] font-bold text-[#4cd7f6]">{project.projectCode}</span><h2 className="text-[20px] font-bold text-[#dae2fd]">{project.name}</h2></div><div className="mt-1.5 flex flex-wrap items-center gap-4 text-[12px] text-[#8e909f]"><span>项目经理：<strong className="text-[#dae2fd]">{project.managerName}</strong></span><span>工程地点：<strong className="text-[#dae2fd]">{project.location}</strong></span><span>建设阶段：<strong className="text-[#4cd7f6]">{project.constructionStage}</strong></span><span>综合评级：<strong className="text-[#10B981]">{project.healthGrade}</strong></span></div></div>
          <div className="text-right"><div className="text-[11px] text-[#8e909f]">合同总投资</div><div className="text-[18px] font-bold font-mono-num text-[#dae2fd]">¥ {(project.totalBudget / 100000000).toFixed(2)} 亿元</div></div>
        </div>
        <div><div className="mb-1 flex justify-between text-[11px] text-[#8e909f]"><span>工程形象进度</span><span className="font-bold text-[#4cd7f6]">{project.progressPercent}%</span></div><div className="h-2 w-full overflow-hidden rounded-full border border-[#444653]/30 bg-[#131b2e]"><div className="h-full rounded-full bg-gradient-to-r from-[#1e40af] to-[#4cd7f6] shadow-[0_0_12px_#4cd7f6] transition-all duration-500" style={{ width: `${project.progressPercent}%` }} /></div></div>
      </section>

      <section className="glass-panel flex flex-col rounded-xl p-5" data-testid="project-tax-analysis-section">
        <div className="mb-4 flex flex-col justify-between gap-3 border-b border-[#444653]/30 pb-4 sm:flex-row sm:items-center">
          <div><h3 className="flex items-center gap-2 text-[17px] font-bold text-[#dae2fd]"><FileCheck2 className="h-4 w-4 text-[#4cd7f6]" /><span>项目全周期税务分析（规范事实）</span></h3><p className="mt-0.5 text-[12px] text-[#8e909f]">直接展示后端项目管理边界确定性投影，不在前端重算增值税管理头寸。</p></div>
          <div>{taxAnalysisStatus === 'degraded' ? <span className="rounded border border-[#F59E0B]/30 bg-[#F59E0B]/15 px-2 py-0.5 text-[11px] font-medium text-[#F59E0B]">⚠️ 数据降级运行</span> : <span className="rounded border border-[#10B981]/30 bg-[#10B981]/15 px-2 py-0.5 text-[11px] font-medium text-[#10B981]">🛡️ 规范事实唯一来源</span>}</div>
        </div>

        <div className="mb-4 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2" role="note"><p className="text-[12px] font-bold text-[#F59E0B]">项目管理边界 · 项目管理口径 · 非申报依据</p><p className="mt-1 text-[11px] text-[#c4c5d5]">所有增值税数值均直接来自后端项目边界投影，仅用于项目管理分析。</p></div>

        {taxAnalysisStatus === 'loading' && <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-[#8e909f]"><Loader2 className="h-4 w-4 animate-spin text-[#4cd7f6]" /><span>正在加载项目税务分析事实数据…</span></div>}
        {taxAnalysisStatus === 'failed' && <div className="flex items-center gap-2 rounded-lg border border-[#EF4444]/30 bg-[#EF4444]/10 p-4 text-[13px] text-[#ffb4ab]"><AlertTriangle className="h-4 w-4 flex-shrink-0" /><span>{taxAnalysisMessage || '加载项目税务分析数据失败'}</span></div>}
        {taxAnalysisStatus === 'empty' && <div className="rounded-lg border border-[#444653]/20 bg-[#131b2e]/40 p-6 text-center text-[13px] text-[#8e909f]">{taxAnalysisMessage || '该工程项目暂无规范发票事实记录。'}</div>}

        {(taxAnalysisStatus === 'ready' || taxAnalysisStatus === 'degraded') && taxAnalysis && (
          <div className="space-y-4 font-mono-num">
            {taxAnalysisStatus === 'degraded' && taxAnalysis.dataGaps.length > 0 && <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/10 p-3 text-[12px] font-sans text-[#F59E0B]"><p className="mb-1 flex items-center gap-1.5 font-bold"><AlertTriangle className="h-3.5 w-3.5" /><span>数据完整性缺口提示：</span></p><ul className="list-inside list-disc space-y-0.5 text-[#dae2fd]/80">{taxAnalysis.dataGaps.map((gap, i) => <li key={i}>{dataGapLabel(gap)}</li>)}</ul></div>}

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] p-3.5"><div className="text-[11px] font-sans text-[#8e909f]">开具销项（不含税）</div><div className="mt-1 text-[15px] font-bold text-[#dae2fd]">¥ {taxAnalysis.outInvoiceNet.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div><div className="mt-0.5 text-[11px] text-[#4cd7f6]">销项税额：¥ {taxAnalysis.outInvoiceVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div>
              <div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] p-3.5"><div className="text-[11px] font-sans text-[#8e909f]">取得进项（不含税）</div><div className="mt-1 text-[15px] font-bold text-[#dae2fd]">¥ {taxAnalysis.inInvoiceNet.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div><div className="mt-0.5 text-[11px] text-[#4cd7f6]">进项税额：¥ {taxAnalysis.inInvoiceVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-3" data-testid="input-vat-breakdown">
              <div className="rounded-lg border border-[#10B981]/30 bg-[#10B981]/5 p-3.5"><div className="text-[11px] font-sans text-[#c4c5d5]">已确认可抵扣进项税额</div><div className="mt-1 text-[15px] font-bold text-[#10B981]">¥ {taxAnalysis.deductibleInputVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div>
              <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 p-3.5" data-testid="pending-input-vat"><div className="text-[11px] font-sans text-[#c4c5d5]">待认证／待判定进项税额</div><div className="mt-1 text-[15px] font-bold text-[#F59E0B]">¥ {taxAnalysis.pendingInputVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div>
              <div className="rounded-lg border border-[#EF4444]/30 bg-[#EF4444]/5 p-3.5" data-testid="nondeductible-input-vat"><div className="text-[11px] font-sans text-[#c4c5d5]">不可抵扣进项税额</div><div className="mt-1 text-[15px] font-bold text-[#ffb4ab]">¥ {taxAnalysis.nondeductibleInputVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-3"><div className="rounded-lg border border-[#444653]/20 bg-[#0b1326]/50 p-3"><div className="text-[11px] font-sans text-[#8e909f]">已入账进项税额</div><div className="mt-1 text-[14px] font-bold text-[#dae2fd]">¥ {taxAnalysis.inputVatAccounted.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div><div className="rounded-lg border border-[#444653]/20 bg-[#0b1326]/50 p-3"><div className="text-[11px] font-sans text-[#8e909f]">未入账进项税额</div><div className="mt-1 text-[14px] font-bold text-[#dae2fd]">¥ {taxAnalysis.inputVatUnaccounted.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div><div className={`rounded-lg border p-3 ${taxAnalysis.inputVatIdentityOk ? 'border-[#10B981]/30 bg-[#10B981]/5' : 'border-[#EF4444]/30 bg-[#EF4444]/5'}`}><div className="text-[11px] font-sans text-[#8e909f]">进项一致性核验标识</div><div className={`mt-1 text-[13px] font-bold font-sans ${taxAnalysis.inputVatIdentityOk ? 'text-[#10B981]' : 'text-[#ffb4ab]'}`}>{taxAnalysis.inputVatIdentityOk ? '进项一致性核验：通过' : '进项一致性核验：未通过'}</div></div></div>

            <div className="rounded-lg border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-4" data-testid="signed-vat-position"><div className="text-[12px] font-sans text-[#c4c5d5]">{taxAnalysis.signedVatPosition > 0 ? '净销项增值税管理头寸' : taxAnalysis.signedVatPosition < 0 ? '净进项增值税管理头寸' : '增值税管理头寸平衡'}</div><div className="mt-1 text-[20px] font-bold text-[#4cd7f6]">¥ {taxAnalysis.signedVatPosition.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div><div className="mt-1 text-[10px] font-sans text-[#8e909f]">直接使用后端确定性管理头寸结果，不进行前端二次计算。</div></div>

            <div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] p-4" data-testid="internal-elimination-card"><div className="text-[12px] font-bold font-sans text-[#dae2fd]">内部交易抵消</div>{taxAnalysis.internalEliminatedNet === 0 && taxAnalysis.internalEliminatedVat === 0 ? <div className="mt-2 text-[12px] font-sans text-[#8e909f]">本项目无内部交易抵消</div> : <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2"><div><div className="text-[11px] font-sans text-[#8e909f]">抵消净额</div><div className="text-[14px] font-bold text-[#dae2fd]">¥ {taxAnalysis.internalEliminatedNet.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div><div><div className="text-[11px] font-sans text-[#8e909f]">抵消增值税</div><div className="text-[14px] font-bold text-[#dae2fd]">¥ {taxAnalysis.internalEliminatedVat.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div></div></div>}</div>

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#444653]/20 bg-[#0b1326]/40 p-3 text-[12px]"><div className="flex flex-wrap items-center gap-4 text-[#c4c5d5]">{taxAnalysis.realCost > 0 ? <span>外部真实成本：<b className="text-[#dae2fd]">¥ {taxAnalysis.realCost.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b></span> : <span className="font-bold font-sans text-[#F59E0B]">降级运行 · 实际成本尚未归集入账</span>}<span>发票事实记录：<b className="text-[#4cd7f6]">{taxAnalysis.invoiceCount}</b> 笔</span><span>数据真实源：<b className="text-[#dae2fd]">规范事实视图</b></span></div><div className={`flex items-center gap-1 text-[11px] font-medium ${taxAnalysis.legacyTablesUsed ? 'text-[#F59E0B]' : 'text-[#10B981]'}`}>{taxAnalysis.legacyTablesUsed ? <AlertTriangle className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}<span>{taxAnalysis.legacyTablesUsed ? '检测到旧表依赖' : '无旧混合数据表依赖'}</span></div></div>
          </div>
        )}
      </section>

      <section className="glass-panel rounded-xl p-5" data-testid="counterparty-section">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-[#444653]/30 pb-3"><div><h3 className="flex items-center gap-2 text-[17px] font-bold text-[#dae2fd]"><Building2 className="h-4 w-4 text-[#4cd7f6]" />项目往来对手方明细</h3><p className="mt-0.5 text-[12px] text-[#c4c5d5]">严格按知识库数据库真实存在的合同、发票、收付款、履约记录聚合；系统内主体与同步出的外部单位都来自同一份业务数据库。</p></div><div className="flex items-center gap-2"><span className="text-[11px] text-[#8e909f]">归属</span><div className="flex rounded-lg border border-[#444653]/30 bg-[#131b2e] p-0.5">{(['全部', '系统内', '系统外'] as const).map(value => <button key={value} type="button" onClick={() => setCounterpartyFilter(value)} className={`rounded-md px-2.5 py-1 text-[11px] transition-colors ${counterpartyFilter === value ? 'border border-[#4cd7f6]/40 bg-[#4cd7f6]/15 text-[#4cd7f6]' : 'text-[#8e909f] hover:text-[#dae2fd]'}`}>{value}</button>)}</div></div></div>

        {counterpartyStatus === 'loading' && <div className="py-4 text-[12px] text-[#8e909f]">对手方数据加载中…</div>}
        {counterpartyStatus === 'failed' && <div className="rounded-lg border border-[#EF4444]/30 bg-[#EF4444]/10 p-3 text-[12px] text-[#EF4444]">对手方数据加载失败：{counterpartyMessage}</div>}
        {counterpartyStatus === 'empty' && <div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] p-3 text-[12px] text-[#8e909f]">{counterpartyMessage || '该项目当前没有任何合同、发票、收付款数据。'}</div>}

        {counterpartyStatus === 'ready' && <><div className="mb-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-5">{[
          ['合同金额合计', counterpartTotals.contractAmount, '#dae2fd'], ['进项税额合计', counterpartTotals.invoiceInVat, '#4cd7f6'], ['销项税额合计', counterpartTotals.invoiceOutVat, '#4cd7f6'], ['对外付款合计', counterpartTotals.cashflowOutAmount, '#dae2fd'], ['外部成本合计', counterpartTotals.realCostAmount, '#a78bfa'],
        ].map(([title, value, color]) => <div key={String(title)} className="rounded-lg border border-[#444653]/30 bg-[#131b2e] p-2"><p className="text-[#8e909f]">{title}</p><p className="mt-0.5 font-semibold" style={{ color: String(color) }}>¥ {Number(value).toLocaleString('zh-CN')}</p></div>)}</div><div className="overflow-x-auto"><table className="w-full text-left text-[12px]"><thead><tr className="border-b border-[#444653]/30 text-[#8e909f]"><th className="w-[180px] px-3 py-2">对手方编码／名称</th><th className="w-[90px] px-2 py-2 text-center">归属</th><th className="w-[60px] px-2 py-2 text-center">合同数</th><th className="w-[110px] px-2 py-2 text-right">合同金额</th><th className="w-[80px] px-2 py-2 text-right">进项发票／税额</th><th className="w-[80px] px-2 py-2 text-right">销项发票／税额</th><th className="w-[80px] px-2 py-2 text-right">收款／付款</th><th className="w-[90px] px-2 py-2 text-right">外部成本</th><th className="w-[70px] px-2 py-2 text-right">履约记录</th></tr></thead><tbody>{filteredCounterparties.length === 0 && <tr><td colSpan={9} className="px-3 py-3 text-center text-[#8e909f]">当前筛选条件下没有对手方。</td></tr>}{filteredCounterparties.map(party => <tr key={party.partyCode} className="border-b border-[#444653]/20 hover:bg-[#131b2e]/40"><td className="px-3 py-2"><div className="break-all font-semibold text-[#dae2fd]">{party.partyCode}</div><div className="whitespace-normal break-words text-[11px] text-[#8e909f]">{party.partyName}</div></td><td className="px-2 py-2 text-center"><span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-medium ${party.isInternal ? 'border-[#10B981]/30 bg-[#10B981]/15 text-[#10B981]' : 'border-[#8b5cf6]/30 bg-[#8b5cf6]/15 text-[#a78bfa]'}`}>{party.isInternal ? '🏢 系统内' : '🌐 系统外'}</span></td><td className="px-2 py-2 text-center text-[#dae2fd]">{party.contractCount}</td><td className="px-2 py-2 text-right text-[#dae2fd]">¥ {party.contractAmount.toLocaleString('zh-CN')}</td><td className="px-2 py-2 text-right"><div className="text-[#dae2fd]">{party.invoiceInCount} 张</div><div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceInVat.toLocaleString('zh-CN')}</div></td><td className="px-2 py-2 text-right"><div className="text-[#dae2fd]">{party.invoiceOutCount} 张</div><div className="text-[11px] text-[#4cd7f6]">税额 ¥ {party.invoiceOutVat.toLocaleString('zh-CN')}</div></td><td className="px-2 py-2 text-right"><div className="text-[#dae2fd]">收 ¥ {party.cashflowInAmount.toLocaleString('zh-CN')}</div><div className="text-[11px] text-[#ffb4ab]">付 ¥ {party.cashflowOutAmount.toLocaleString('zh-CN')}</div></td><td className="px-2 py-2 text-right"><div className="text-[#a78bfa]">{party.realCostCount} 笔</div><div className="text-[11px] text-[#a78bfa]">¥ {party.realCostAmount.toLocaleString('zh-CN')}</div></td><td className="px-2 py-2 text-right"><div className="text-[#dae2fd]">{party.fulfillmentCount} 条</div><div className="text-[11px] text-[#8e909f]">¥ {party.fulfillmentAmount.toLocaleString('zh-CN')}</div></td></tr>)}</tbody></table></div></>}
      </section>

      <section className="glass-panel rounded-xl p-5">
        <div className="mb-4 flex items-center justify-between border-b border-[#444653]/30 pb-3"><div><h3 className="text-[17px] font-bold text-[#dae2fd]">成本渗透率矩阵（工作任务分解结构）</h3><p className="text-[12px] text-[#c4c5d5]">逐级穿透工程科目预算计划、实际发生成本与偏差率</p></div></div>
        {effectiveCostItems.length > 0 ? <div className="overflow-x-auto rounded-xl border border-[#444653]/30 bg-[#131b2e]/90"><table className="w-full min-w-[760px] border-collapse text-left text-[13px] font-mono-num"><thead className="bg-[#171f33] text-[12px] font-semibold text-[#8e909f]"><tr><th className="whitespace-nowrap px-4 py-3">成本科目（工作任务分解编码）</th><th className="whitespace-nowrap px-4 py-3 text-right">计划预算</th><th className="whitespace-nowrap px-4 py-3 text-right">实际发生成本</th><th className="whitespace-nowrap px-4 py-3 text-right">偏差率 <span className="text-[10px] font-normal text-[#ffb59a]">（止付线：+{stopPayThreshold}%）</span></th><th className="whitespace-nowrap px-4 py-3">责任工程师</th><th className="whitespace-nowrap px-4 py-3 text-center">状态／管控指令</th></tr></thead><tbody className="divide-y divide-[#444653]/20">{effectiveCostItems.map(item => { const isOverThreshold = item.variancePercent >= stopPayThreshold; const isWarning = item.status === '超支预警' || isOverThreshold; const isSaved = item.status === '节约支出'; return <tr key={item.id} className={`transition-colors hover:bg-[#222a3d]/40 ${item.level === 2 ? 'bg-[#171f33]/30' : 'font-semibold'} ${isOverThreshold ? 'bg-[#EF4444]/5' : ''}`}><td className={`whitespace-nowrap px-4 py-3 ${item.level === 2 ? 'pl-8 text-[#dae2fd]' : 'text-[#dde1ff]'}`}>{item.code} {item.level === 1 ? `（${item.name}）` : ''}</td><td className="whitespace-nowrap px-4 py-3 text-right text-[#8e909f]">¥ {item.plannedAmount.toLocaleString('zh-CN')}</td><td className="whitespace-nowrap px-4 py-3 text-right text-[#dae2fd]">¥ {item.actualAmount.toLocaleString('zh-CN')}</td><td className={`whitespace-nowrap px-4 py-3 text-right font-bold ${isWarning ? 'text-[#EF4444]' : isSaved ? 'text-[#10B981]' : 'text-[#4cd7f6]'}`}>{item.variancePercent > 0 ? `+${item.variancePercent}%` : item.variancePercent === 0 ? '持平' : `${item.variancePercent}%`}</td><td className="whitespace-nowrap px-4 py-3 text-[#c4c5d5]">{item.manager}</td><td className="whitespace-nowrap px-4 py-3 text-center"><span className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-2 py-0.5 text-[11px] ${isOverThreshold ? 'animate-pulse border-[#EF4444]/40 bg-[#EF4444]/20 font-bold text-[#ffb4ab]' : isWarning ? 'border-[#EF4444]/30 bg-[#EF4444]/15 text-[#EF4444]' : isSaved ? 'border-[#10B981]/30 bg-[#10B981]/15 text-[#10B981]' : 'border-[#4cd7f6]/30 bg-[#03b5d3]/15 text-[#4cd7f6]'}`}>{isOverThreshold ? '【已触发止付令】' : item.status}</span></td></tr>; })}</tbody></table></div> : <div className="rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/10 p-6 text-[13px] font-sans text-[#F59E0B]" data-testid="wbs-degraded-state" role="status" aria-live="polite"><p className="mb-2 flex items-center gap-2 font-bold"><AlertTriangle className="h-4 w-4" /><span>降级运行 · 实际成本尚未归集入账</span></p><p className="leading-relaxed text-[#dae2fd]/80">暂无可验证成本分解数据。工作任务分解成本必须来源于系统中已验收的真实业务事实；未来真实数据也可能来自确定性业务表或预算系统，不限定必须由规范事实录入。系统不会自动生成预算、成本、责任人或偏差率。</p></div>}
      </section>

      {showDeleteConfirmModal && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"><div className="relative w-full max-w-lg space-y-4 rounded-2xl border border-[#ef4444]/40 bg-[#131b2e] p-6 shadow-2xl"><div className="flex items-center gap-3 border-b border-[#444653]/40 pb-3 text-[#ef4444]"><div className="rounded-lg border border-[#ef4444]/30 bg-[#ef4444]/10 p-2"><AlertTriangle className="h-6 w-6" /></div><div><h3 className="text-[17px] font-bold text-[#dae2fd]">危险操作：清空／删除项目财税数据</h3><p className="text-[12px] text-[#fca5a5]">清空该项目在税务系统中的全部台账，底层凭证知识库保持安全不变</p></div></div><div className="space-y-2 rounded-xl border border-[#ef4444]/20 bg-[#0b1326]/60 p-4 text-[13px] text-[#c4c5d5]"><p className="font-semibold text-[#dae2fd]">目标项目：<span className="text-[#4cd7f6]">{project.projectCode} · {project.name}</span></p><div className="space-y-1 text-[12px] text-[#8e909f]"><p>将一次性删除税务系统中的以下关联数据：</p><ul className="list-inside list-disc space-y-0.5 text-[#dae2fd]/80"><li>正式采购与工程分包合同台账</li><li>增值税专用发票及进销项记录</li><li>银行公对公转账回单与资金流水</li><li>四流一致性匹配台账与风险预警</li><li>智能体检批次与审计事实快照</li></ul></div><p className="rounded-lg border border-[#10b981]/20 bg-[#10b981]/10 p-2 pt-1 text-[11px] leading-relaxed text-[#10b981]">🛡️ <b>知识库数据库保护</b>：原始凭证库、扫描文件与文字识别知识库<b>完全不受任何影响</b>。清空后可通过【凭证知识库检索同步】重新清洗装载。</p></div><div className="space-y-1.5 pt-1"><label className="flex items-center justify-between text-[12px] font-semibold text-[#dae2fd]"><span>🔐 请输入当前账号登录密码进行安全验证：</span></label><input type="password" value={deletePassword} onChange={e => setDeletePassword(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !isDeletingData && deletePassword.trim()) void handleDeleteProjectData(); }} placeholder="请输入登录密码（默认：888888）" className="w-full rounded-lg border border-[#ef4444]/40 bg-[#0b1326] px-3 py-2.5 text-[13px] text-[#dae2fd] outline-none transition-all focus:border-[#ef4444]" disabled={isDeletingData} autoFocus /></div>{deleteErrorNotice && <div className="rounded-lg border border-[#ef4444]/40 bg-[#ef4444]/15 p-3 text-[12px] text-[#fca5a5]">{deleteErrorNotice}</div>}<div className="flex justify-end gap-3 pt-2"><button type="button" onClick={() => { if (!isDeletingData) { setShowDeleteConfirmModal(false); setDeleteErrorNotice(null); } }} disabled={isDeletingData} className="cursor-pointer rounded-lg bg-[#2d3449] px-4 py-2 text-[13px] text-[#dae2fd] hover:bg-[#31394d] disabled:opacity-50">取消</button><button type="button" onClick={() => void handleDeleteProjectData()} disabled={isDeletingData} className="flex cursor-pointer items-center gap-1.5 rounded-lg bg-[#ef4444] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_0_15px_rgba(239,68,68,0.4)] hover:bg-[#dc2626] disabled:opacity-50">{isDeletingData ? <><Loader2 className="h-4 w-4 animate-spin" /><span>正在清空数据…</span></> : <><Trash2 className="h-4 w-4" /><span>确认清空项目数据</span></>}</button></div></div></div>}
    </div>
  );
}
