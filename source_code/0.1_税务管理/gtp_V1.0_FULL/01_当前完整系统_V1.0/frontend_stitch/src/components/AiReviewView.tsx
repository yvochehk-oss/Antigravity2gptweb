import { useEffect, useState } from 'react';
import { BrainCircuit, RefreshCw, Stethoscope } from 'lucide-react';
import { ApiError, extractAiExecutionMetadata, runAiReview, runHealthCheck } from '../api';
import { AiExecutionMetadata, DataStatus, ProjectItem } from '../types';
import { DataStatusCard } from './DataStatusCard';
import { dataStatusLabel, enumLabel, fieldLabel, priorityLabel, severityLabel } from './uiLocalization';

interface AiReviewViewProps {
  projects?: ProjectItem[];
  dataStatus: DataStatus;
  onAskAiAboutRisk?: (entityName: string) => void;
}

const scopes = [
  ['overview', '项目总体经营'], ['budget', '预算与成本偏差'], ['contract', '合同'], ['fulfillment', '履约证据'],
  ['invoice', '发票'], ['cashflow', '资金与付款'], ['cost', '真实成本穿透'], ['tax', '税务管理测算'],
  ['eac', '预计完工成本'], ['risk', '风险事件'], ['material', '材料'], ['labor', '劳务'],
  ['equipment', '设备'], ['subcontract', '专业分包'], ['whole_project', '整个项目'],
] as const;

type ApiResult = Record<string, unknown>;

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return '结构化结果';
  if (typeof value === 'string') return enumLabel(value);
  return String(value);
}

function endpointLabel(endpoint: AiExecutionMetadata['effectiveEndpoint']): string {
  if (!endpoint) return '—';
  return endpoint.id !== undefined ? `模型端点 #${endpoint.id}` : '后端动态模型端点';
}

function dataGapLabel(value: unknown): string {
  const gap = typeof value === 'string' ? value : '';
  switch (gap) {
    case 'INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW': return '进项税额合规性待确认';
    case 'INPUT_VAT_ACCOUNTING_IDENTITY_FAILED': return '进项税额勾稽平衡校验异常';
    case 'CANONICAL_INVOICE_PERIOD_MISSING': return '规范发票事实缺少有效期间';
    default: return gap ? enumLabel(gap) : '存在待补充数据';
  }
}

function ExecutionMetadataPanel({ data }: { data: ApiResult }) {
  const metadata = extractAiExecutionMetadata(data);
  const attempts = Array.isArray(metadata.attempts) ? metadata.attempts : [];
  if (Object.keys(metadata).length === 0) return null;
  return (
    <div className="glass-panel rounded-xl border border-[#F59E0B]/30 p-4 text-[12px]">
      <h4 className="font-bold text-[#dae2fd]">模型执行信息（后端返回）</h4>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[#c4c5d5]">
        {(metadata.selectedEndpoint || metadata.effectiveEndpoint) && <span>实际端点：{endpointLabel(metadata.effectiveEndpoint ?? metadata.selectedEndpoint)}</span>}
        {metadata.status && <span>状态：{dataStatusLabel(metadata.status)}</span>}
        {metadata.fallback !== undefined && <span>启用回退机制：{metadata.fallback ? '是' : '否'}</span>}
        {metadata.degraded !== undefined && <span>降级运行：{metadata.degraded ? '是' : '否'}</span>}
      </div>
      {attempts.length > 0 && <div className="mt-2 text-[#aeb5ca]">尝试摘要：{attempts.map((attempt, index) => `${index + 1}. ${endpointLabel(attempt.endpoint)}${attempt.status ? `／${dataStatusLabel(attempt.status)}` : ''}${attempt.error ? '：执行失败' : ''}`).join('；')}</div>}
    </div>
  );
}

function FindingsSection({ items }: { items: any[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="glass-panel space-y-3 rounded-xl border border-[#444653]/30 p-5">
      <h4 className="flex items-center gap-2 text-[15px] font-bold text-[#dae2fd]"><span>🔍 审查发现与风险项清单</span><span className="rounded-full border border-[#444653]/40 bg-[#222a3d] px-2 py-0.5 text-[11px] text-[#dae2fd]">{items.length} 项</span></h4>
      <div className="grid grid-cols-1 gap-3">
        {items.map((item, idx) => {
          const sev = String(item?.severity || 'MEDIUM').toUpperCase();
          const isCritical = sev === 'CRITICAL' || sev === 'HIGH';
          const isLow = sev === 'LOW';
          return (
            <div key={idx} className="space-y-2 rounded-xl border border-[#444653]/40 bg-[#131b2e] p-4 transition-colors hover:border-[#4cd7f6]/40">
              <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${isCritical ? 'border-[#EF4444]/40 bg-[#EF4444]/20 text-[#ff8e8e]' : isLow ? 'border-[#10B981]/40 bg-[#10B981]/20 text-[#6ee7b7]' : 'border-[#F59E0B]/40 bg-[#F59E0B]/20 text-[#fcd34d]'}`}>{severityLabel(sev)}</span><span className="text-[13px] font-bold text-[#dae2fd]">{item?.area || '综合领域'} · {item?.issue || '风险项'}</span></div></div>
              {item?.evidence && <div className="rounded-lg border border-[#444653]/20 bg-[#0b1326] p-2.5 text-[12px] text-[#c4c5d5]"><span className="font-semibold text-[#8e909f]">【数据依据】：</span>{item.evidence}</div>}
              {item?.impact && <div className="rounded-lg border border-[#ef4444]/20 bg-[#ef4444]/5 p-2.5 text-[12px] text-[#f87171]"><span className="font-semibold">【潜在影响】：</span>{item.impact}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RecommendationsSection({ items }: { items: any[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="glass-panel space-y-3 rounded-xl border border-[#444653]/30 p-5">
      <h4 className="flex items-center gap-2 text-[15px] font-bold text-[#dae2fd]"><span>💡 管理建议与整改指令</span><span className="rounded-full border border-[#444653]/40 bg-[#222a3d] px-2 py-0.5 text-[11px] text-[#dae2fd]">{items.length} 条</span></h4>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {items.map((item, idx) => {
          const pri = String(item?.priority || 'P1').toUpperCase();
          const isP0 = pri === 'P0';
          return (
            <div key={idx} className="space-y-2 rounded-xl border border-[#444653]/40 bg-[#131b2e] p-4">
              <div className="flex items-center justify-between"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${isP0 ? 'border-[#EF4444]/40 bg-[#EF4444]/20 font-extrabold text-[#ff8e8e]' : 'border-[#3B82F6]/40 bg-[#3B82F6]/20 text-[#93c5fd]'}`}>{priorityLabel(pri)}</span>{item?.owner && <span className="rounded border border-[#444653]/30 bg-[#222a3d] px-2 py-0.5 text-[11px] text-[#8e909f]">责任：{item.owner}</span>}</div>
              <p className="text-[13px] font-semibold text-[#dae2fd]">{item?.action || '—'}</p>
              {item?.reason && <p className="text-[11px] leading-relaxed text-[#8e909f]">原因：{item.reason}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DataGapsSection({ items }: { items: any[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="glass-panel space-y-3 rounded-xl border border-[#444653]/30 p-5">
      <h4 className="flex items-center gap-2 text-[14px] font-bold text-[#dae2fd]"><span>📑 待补充材料与数据缺口</span><span className="rounded-full border border-[#444653]/40 bg-[#222a3d] px-2 py-0.5 text-[11px] text-[#dae2fd]">{items.length} 项</span></h4>
      <div className="flex flex-wrap gap-2">{items.map((gap, idx) => <span key={idx} className="rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-1.5 text-[12px] text-[#c4c5d5]">• {dataGapLabel(gap)}</span>)}</div>
    </div>
  );
}

function JobsSection({ jobs }: { jobs: any[] }) {
  if (!jobs || jobs.length === 0) return null;
  return (
    <div className="glass-panel space-y-3 rounded-xl border border-[#444653]/30 p-5">
      <h4 className="flex items-center justify-between text-[14px] font-bold text-[#dae2fd]"><span className="flex items-center gap-2">⚙️ 子维度体检任务执行状态</span><span className="rounded-full border border-[#444653]/40 bg-[#222a3d] px-2 py-0.5 text-[11px] text-[#dae2fd]">{jobs.length} 项子任务</span></h4>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
        {jobs.map((job, idx) => {
          const isDone = job.status === 'completed';
          const isRunning = job.status === 'running' || job.status === 'pending';
          return <div key={idx} className="flex items-center justify-between rounded-lg border border-[#444653]/30 bg-[#131b2e] p-3"><div><span className="block text-[12px] font-bold text-[#dae2fd]">维度：{enumLabel(String(job.scope ?? '综合'))}</span><span className="text-[10px] text-[#8e909f]">模型端点：#{job.requested_endpoint_id || job.endpoint_id || '—'}</span></div><span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${isDone ? 'border border-[#10b981]/30 bg-[#10b981]/20 text-[#34d399]' : isRunning ? 'animate-pulse border border-[#3b82f6]/30 bg-[#3b82f6]/20 text-[#60a5fa]' : 'border border-[#ef4444]/30 bg-[#ef4444]/20 text-[#f87171]'}`}>{job.status === 'completed' ? '已完成' : job.status === 'running' ? '执行中…' : job.status === 'pending' ? '排队中' : '失败'}</span></div>;
        })}
      </div>
    </div>
  );
}

function ResultPanel({ title, data }: { title: string; data: ApiResult | null }) {
  if (!data) return <DataStatusCard status="UNAVAILABLE" title={`${title}尚无结果`} message="执行后端任务后，真实结果会显示在这里。" />;
  const result = (data.result && typeof data.result === 'object' ? data.result : data.consensus && typeof data.consensus === 'object' ? data.consensus : data) as ApiResult;
  const findings = Array.isArray(result.findings) ? result.findings : Array.isArray(result.common_findings) ? result.common_findings : [];
  const recommendations = Array.isArray(result.recommendations) ? result.recommendations : [];
  const dataGaps = Array.isArray(result.data_gaps) ? result.data_gaps : Array.isArray(data.data_gaps) ? data.data_gaps : [];
  const jobs = Array.isArray(data.jobs) ? data.jobs : [];
  const scalarEntries = Object.entries(result).filter(([key, value]) => !Array.isArray(value) && typeof value !== 'object' && key !== 'raw_response' && key !== 'summary' && key !== 'status' && key !== 'requires_manual_review');
  const otherArrayEntries = Object.entries(result).filter(([key, value]) => Array.isArray(value) && !['findings', 'common_findings', 'recommendations', 'data_gaps', 'jobs', 'attempts'].includes(key));

  return (
    <div className="space-y-4">
      <ExecutionMetadataPanel data={data} />
      <div className="glass-panel space-y-4 rounded-xl border border-[#4cd7f6]/30 p-5">
        <div className="flex items-center justify-between"><h3 className="text-[15px] font-bold text-[#dae2fd]">{title}（真实接口返回）</h3>{result.overall_risk && <span className="rounded-full border border-[#3b82f6]/40 bg-[#3b82f6]/20 px-2.5 py-0.5 text-[12px] font-bold text-[#60a5fa]">{enumLabel(String(result.overall_risk))}</span>}{result.risk_level && <span className="rounded-full border border-[#3b82f6]/40 bg-[#3b82f6]/20 px-2.5 py-0.5 text-[12px] font-bold text-[#60a5fa]">{enumLabel(String(result.risk_level))}</span>}</div>
        {result.summary && <div className="rounded-lg border border-[#444653]/40 bg-[#131b2e] p-3.5"><p className="mb-1 text-[11px] font-semibold text-[#8e909f]">📋 审查综合研判／总体摘要</p><p className="text-[13px] leading-relaxed text-[#dae2fd]">{String(result.summary)}</p></div>}
        <div className="grid grid-cols-2 gap-3 text-[12px] md:grid-cols-4">{scalarEntries.map(([key, value]) => <div key={key} className="rounded-lg bg-[#131b2e] p-3"><p className="text-[#8e909f]">{fieldLabel(key)}</p><p className="mt-1 break-all font-semibold text-[#dae2fd]">{renderValue(value)}</p></div>)}</div>
      </div>
      {findings.length > 0 && <FindingsSection items={findings} />}
      {recommendations.length > 0 && <RecommendationsSection items={recommendations} />}
      {dataGaps.length > 0 && <DataGapsSection items={dataGaps} />}
      {jobs.length > 0 && <JobsSection jobs={jobs} />}
      {otherArrayEntries.map(([key, value]) => <div key={key} className="glass-panel rounded-xl border border-[#444653]/30 p-5"><h4 className="mb-2 text-[14px] font-bold text-[#dae2fd]">{fieldLabel(key)}</h4><p className="text-[12px] text-[#c4c5d5]">共 {Array.isArray(value) ? value.length : 0} 条结构化明细。原始技术字段不在业务界面直接展示。</p></div>)}
    </div>
  );
}

export function AiReviewView({ projects = [], dataStatus }: AiReviewViewProps) {
  const [activeTab, setActiveTab] = useState<'single' | 'health'>('single');
  const [projectId, setProjectId] = useState('');
  const [scope, setScope] = useState('tax');
  const [instruction, setInstruction] = useState('');
  const [healthProfile, setHealthProfile] = useState('standard');
  const [healthInstruction, setHealthInstruction] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [reviewResult, setReviewResult] = useState<ApiResult | null>(null);
  const [healthResult, setHealthResult] = useState<ApiResult | null>(null);
  const [statusMessage, setStatusMessage] = useState('尚未执行真实智能任务。');
  const [status, setStatus] = useState<DataStatus>(dataStatus === 'READY' ? 'READY' : dataStatus);

  useEffect(() => { setProjectId(projects[0]?.id ?? ''); setReviewResult(null); setHealthResult(null); }, [projects]);
  useEffect(() => { setStatus(dataStatus); if (dataStatus !== 'READY') setStatusMessage('没有真实项目数据，智能任务不可启动。'); }, [dataStatus]);

  const runSingleReview = async () => {
    const project = projects.find(item => item.id === projectId);
    if (!project) { setStatus('DEGRADED'); setStatusMessage('请选择真实项目。'); return; }
    setIsRunning(true); setStatusMessage('正在执行智能专项审查…');
    try {
      const data = await runAiReview({ projectId: project.numericId, scope, instruction });
      setReviewResult(data);
      const resultStatus = typeof data.status === 'string' ? data.status : 'READY';
      setStatus(resultStatus === 'DEGRADED' || resultStatus === 'UNAVAILABLE' ? resultStatus : 'READY');
      setStatusMessage(resultStatus === 'READY' ? '专项审查结果来自税务智能审查接口。' : `专项审查当前为${dataStatusLabel(resultStatus)}，请结合端点执行信息人工复核。`);
    } catch (error) {
      setReviewResult(null); setStatus('DEGRADED'); setStatusMessage(error instanceof ApiError ? error.message : '专项审查接口调用失败。');
    } finally { setIsRunning(false); }
  };

  const runHealth = async () => {
    const project = projects.find(item => item.id === projectId);
    if (!project) { setStatus('DEGRADED'); setStatusMessage('请选择真实项目。'); return; }
    setIsRunning(true); setStatusMessage('正在执行多模型智能体检…');
    try {
      const data = await runHealthCheck({ projectId: project.numericId, profile: healthProfile, instruction: healthInstruction });
      setHealthResult(data);
      const resultStatus = typeof data.status === 'string' ? data.status : 'READY';
      setStatus(resultStatus === 'DEGRADED' || resultStatus === 'UNAVAILABLE' ? resultStatus : 'READY');
      setStatusMessage(resultStatus === 'READY' ? '体检结果来自税务综合体检接口。' : `体检当前为${dataStatusLabel(resultStatus)}，请结合端点执行信息人工复核。`);
    } catch (error) {
      setHealthResult(null); setStatus('DEGRADED'); setStatusMessage(error instanceof ApiError ? error.message : '智能体检接口调用失败。');
    } finally { setIsRunning(false); }
  };

  if (dataStatus !== 'READY' || projects.length === 0) {
    return <div className="space-y-6"><h2 className="text-[26px] font-bold text-[#dae2fd]">智能审查与体检</h2><DataStatusCard status={dataStatus} title="智能审查不可用" message="请先加载真实项目数据。系统不会使用项目、风险或模型的本地演示数据。" /></div>;
  }

  return (
    <div className="space-y-6">
      <div><h2 className="flex items-center gap-2 text-[26px] font-bold text-[#dae2fd]"><BrainCircuit className="h-7 w-7 text-[#4cd7f6]" />智能审查与多模型体检</h2><p className="mt-1 text-[13px] text-[#8e909f]">智能模块只解释真实事实与确定性计算结果；接口失败时保留失败状态，不展示模拟结论。</p></div>
      <div className="flex w-fit gap-2 rounded-xl border border-[#444653]/40 bg-[#131b2e] p-1"><button type="button" onClick={() => setActiveTab('single')} className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'single' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}><BrainCircuit className="mr-1 inline h-4 w-4" />专项审查</button><button type="button" onClick={() => setActiveTab('health')} className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'health' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}><Stethoscope className="mr-1 inline h-4 w-4" />综合体检</button></div>
      <DataStatusCard status={status} title="智能服务状态" message={statusMessage} />

      {activeTab === 'single' && <div className="space-y-5"><div className="glass-panel rounded-xl border border-[#444653]/40 p-5"><div className="grid grid-cols-1 gap-4 text-[12px] sm:grid-cols-2 lg:grid-cols-4"><label className="text-[#8e909f]">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label><label className="text-[#8e909f]">审查范围<select value={scope} onChange={event => setScope(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]">{scopes.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] px-3 py-2 text-[#8e909f]">模型端点：由税务后端模型池动态选择</div><label className="text-[#8e909f]">指导意见<input value={instruction} onChange={event => setInstruction(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]" /></label></div><div className="mt-4 flex justify-end"><button type="button" onClick={() => void runSingleReview()} disabled={isRunning} className="flex items-center gap-2 rounded-xl bg-[#03b5d3] px-5 py-2.5 font-bold text-[#001f26] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动专项审查'}</button></div></div><ResultPanel title="专项审查结果" data={reviewResult} /></div>}

      {activeTab === 'health' && <div className="space-y-5"><div className="glass-panel rounded-xl border border-[#444653]/40 p-5"><div className="grid grid-cols-1 gap-4 text-[12px] sm:grid-cols-2 lg:grid-cols-4"><label className="text-[#8e909f]">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label><label className="text-[#8e909f]">体检档位<select value={healthProfile} onChange={event => setHealthProfile(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]"><option value="quick">快速</option><option value="standard">标准</option><option value="deep">深度</option></select></label><div className="rounded-lg border border-[#444653]/30 bg-[#131b2e] px-3 py-2 text-[#8e909f]">模型端点：由税务后端模型池动态选择</div><label className="text-[#8e909f]">指导意见<input value={healthInstruction} onChange={event => setHealthInstruction(event.target.value)} className="mt-1 w-full rounded-lg border border-[#444653]/40 bg-[#131b2e] px-3 py-2 text-[#dae2fd]" /></label></div><div className="mt-4 flex justify-end"><button type="button" onClick={() => void runHealth()} disabled={isRunning} className="flex items-center gap-2 rounded-xl bg-[#10B981] px-5 py-2.5 font-bold text-[#002114] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动综合体检'}</button></div></div><ResultPanel title="综合体检结果" data={healthResult} /></div>}
    </div>
  );
}
