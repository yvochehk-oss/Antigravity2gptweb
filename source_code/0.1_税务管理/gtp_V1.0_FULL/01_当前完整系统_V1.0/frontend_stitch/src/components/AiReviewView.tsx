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
    <div className="surface-card rounded-xl border border-[var(--color-warning)]/30 p-4 text-[12px]">
      <h4 className="font-bold text-primary">模型执行信息（后端返回）</h4>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-secondary">
        {(metadata.selectedEndpoint || metadata.effectiveEndpoint) && <span>实际端点：{endpointLabel(metadata.effectiveEndpoint ?? metadata.selectedEndpoint)}</span>}
        {metadata.status && <span>状态：{dataStatusLabel(metadata.status)}</span>}
        {metadata.fallback !== undefined && <span>启用回退机制：{metadata.fallback ? '是' : '否'}</span>}
        {metadata.degraded !== undefined && <span>降级运行：{metadata.degraded ? '是' : '否'}</span>}
      </div>
      {attempts.length > 0 && <div className="mt-2 text-secondary">尝试摘要：{attempts.map((attempt, index) => `${index + 1}. ${endpointLabel(attempt.endpoint)}${attempt.status ? `／${dataStatusLabel(attempt.status)}` : ''}${attempt.error ? '：执行失败' : ''}`).join('；')}</div>}
    </div>
  );
}

function FindingsSection({ items }: { items: any[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="surface-card space-y-3 rounded-xl p-5">
      <h4 className="flex items-center gap-2 text-[15px] font-bold text-primary"><span>🔍 审查发现与风险项清单</span><span className="rounded-full border border-default bg-surface-2 px-2 py-0.5 text-[11px] text-primary">{items.length} 项</span></h4>
      <div className="grid grid-cols-1 gap-3">
        {items.map((item, idx) => {
          const sev = String(item?.severity || 'MEDIUM').toUpperCase();
          const isCritical = sev === 'CRITICAL' || sev === 'HIGH';
          const isLow = sev === 'LOW';
          return (
            <div key={idx} className="space-y-2 rounded-xl border border-default bg-surface p-4 transition-colors hover:border-[var(--color-brand)]/50">
              <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${isCritical ? 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 text-[var(--color-danger)]' : isLow ? 'border-[var(--color-success)]/40 bg-[var(--color-success)]/10 text-[var(--color-success)]' : 'border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 text-[var(--color-warning)]'}`}>{severityLabel(sev)}</span><span className="text-[13px] font-bold text-primary">{item?.area || '综合领域'} · {item?.issue || '风险项'}</span></div></div>
              {item?.evidence && <div className="rounded-lg border border-default bg-surface-2 p-2.5 text-[12px] text-secondary"><span className="font-semibold text-muted">【数据依据】：</span>{item.evidence}</div>}
              {item?.impact && <div className="rounded-lg border border-[var(--color-danger)]/20 bg-[var(--color-danger)]/5 p-2.5 text-[12px] text-[var(--color-danger)]"><span className="font-semibold">【潜在影响】：</span>{item.impact}</div>}
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
    <div className="surface-card space-y-3 rounded-xl p-5">
      <h4 className="flex items-center gap-2 text-[15px] font-bold text-primary"><span>💡 管理建议与整改指令</span><span className="rounded-full border border-default bg-surface-2 px-2 py-0.5 text-[11px] text-primary">{items.length} 条</span></h4>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {items.map((item, idx) => {
          const pri = String(item?.priority || 'P1').toUpperCase();
          const isP0 = pri === 'P0';
          return (
            <div key={idx} className="space-y-2 rounded-xl border border-default bg-surface p-4">
              <div className="flex items-center justify-between"><span className={`rounded border px-2 py-0.5 text-[11px] font-bold ${isP0 ? 'border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 font-extrabold text-[var(--color-danger)]' : 'border-[var(--color-brand)]/40 bg-[var(--color-brand-muted)] text-brand'}`}>{priorityLabel(pri)}</span>{item?.owner && <span className="rounded border border-default bg-surface-2 px-2 py-0.5 text-[11px] text-secondary">责任：{item.owner}</span>}</div>
              <p className="text-[13px] font-semibold text-primary">{item?.action || '—'}</p>
              {item?.reason && <p className="text-[11px] leading-relaxed text-secondary">原因：{item.reason}</p>}
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
    <div className="surface-card space-y-3 rounded-xl p-5">
      <h4 className="flex items-center gap-2 text-[14px] font-bold text-primary"><span>📑 待补充材料与数据缺口</span><span className="rounded-full border border-default bg-surface-2 px-2 py-0.5 text-[11px] text-primary">{items.length} 项</span></h4>
      <div className="flex flex-wrap gap-2">{items.map((gap, idx) => <span key={idx} className="rounded-lg border border-default bg-surface px-3 py-1.5 text-[12px] text-secondary">• {dataGapLabel(gap)}</span>)}</div>
    </div>
  );
}

function JobsSection({ jobs }: { jobs: any[] }) {
  if (!jobs || jobs.length === 0) return null;
  return (
    <div className="surface-card space-y-3 rounded-xl p-5">
      <h4 className="flex items-center justify-between text-[14px] font-bold text-primary"><span className="flex items-center gap-2">⚙️ 子维度体检任务执行状态</span><span className="rounded-full border border-default bg-surface-2 px-2 py-0.5 text-[11px] text-primary">{jobs.length} 项子任务</span></h4>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
        {jobs.map((job, idx) => {
          const isDone = job.status === 'completed';
          const isRunning = job.status === 'running' || job.status === 'pending';
          return <div key={idx} className="flex items-center justify-between rounded-lg border border-default bg-surface p-3"><div><span className="block text-[12px] font-bold text-primary">维度：{enumLabel(String(job.scope ?? '综合'))}</span><span className="text-[10px] text-secondary">模型端点：#{job.requested_endpoint_id || job.endpoint_id || '—'}</span></div><span className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${isDone ? 'border-[var(--color-success)]/30 bg-[var(--color-success)]/10 text-[var(--color-success)]' : isRunning ? 'border-[var(--color-info)]/30 bg-[var(--color-info)]/10 text-[var(--color-info)]' : 'border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 text-[var(--color-danger)]'}`}>{job.status === 'completed' ? '已完成' : job.status === 'running' ? '执行中…' : job.status === 'pending' ? '排队中' : '失败'}</span></div>;
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
      <div className="surface-card space-y-4 rounded-xl p-5">
        <div className="flex items-center justify-between"><h3 className="text-[15px] font-bold text-primary">{title}（真实接口返回）</h3>{result.overall_risk && <span className="rounded-full border border-[var(--color-brand)]/40 bg-[var(--color-brand-muted)] px-2.5 py-0.5 text-[12px] font-bold text-brand">{enumLabel(String(result.overall_risk))}</span>}{result.risk_level && <span className="rounded-full border border-[var(--color-brand)]/40 bg-[var(--color-brand-muted)] px-2.5 py-0.5 text-[12px] font-bold text-brand">{enumLabel(String(result.risk_level))}</span>}</div>
        {result.summary && <div className="rounded-lg border border-default bg-surface p-3.5"><p className="mb-1 text-[11px] font-semibold text-secondary">📋 审查综合研判／总体摘要</p><p className="text-[13px] leading-relaxed text-primary">{String(result.summary)}</p></div>}
        <div className="grid grid-cols-2 gap-3 text-[12px] md:grid-cols-4">{scalarEntries.map(([key, value]) => <div key={key} className="rounded-lg border border-default bg-surface p-3"><p className="text-secondary">{fieldLabel(key)}</p><p className="mt-1 break-all font-semibold text-primary">{renderValue(value)}</p></div>)}</div>
      </div>
      {findings.length > 0 && <FindingsSection items={findings} />}
      {recommendations.length > 0 && <RecommendationsSection items={recommendations} />}
      {dataGaps.length > 0 && <DataGapsSection items={dataGaps} />}
      {jobs.length > 0 && <JobsSection jobs={jobs} />}
      {otherArrayEntries.map(([key, value]) => <div key={key} className="surface-card rounded-xl p-5"><h4 className="mb-2 text-[14px] font-bold text-primary">{fieldLabel(key)}</h4><p className="text-[12px] text-secondary">共 {Array.isArray(value) ? value.length : 0} 条结构化明细。原始技术字段不在业务界面直接展示。</p></div>)}
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
    return <div className="space-y-6"><h2 className="text-[26px] font-bold text-primary">智能审查与体检</h2><DataStatusCard status={dataStatus} title="智能审查不可用" message="请先加载真实项目数据。系统不会使用项目、风险或模型的本地演示数据。" /></div>;
  }

  return (
    <div className="space-y-6">
      <div><h2 className="flex items-center gap-2 text-[26px] font-bold text-primary"><BrainCircuit className="h-7 w-7 text-brand" />智能审查与多模型体检</h2><p className="mt-1 text-[13px] text-secondary">智能模块只解释真实事实与确定性计算结果；接口失败时保留失败状态，不展示模拟结论。</p></div>
      <div className="flex w-fit gap-2 rounded-xl border border-default bg-surface p-1"><button type="button" onClick={() => setActiveTab('single')} className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'single' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary hover:text-primary'}`}><BrainCircuit className="mr-1 inline h-4 w-4" />专项审查</button><button type="button" onClick={() => setActiveTab('health')} className={`rounded-lg px-4 py-2 text-[13px] font-semibold ${activeTab === 'health' ? 'bg-[var(--color-brand)] text-white' : 'text-secondary hover:text-primary'}`}><Stethoscope className="mr-1 inline h-4 w-4" />综合体检</button></div>
      <DataStatusCard status={status} title="智能服务状态" message={statusMessage} />

      {activeTab === 'single' && <div className="space-y-5"><div className="surface-card rounded-xl p-5"><div className="grid grid-cols-1 gap-4 text-[12px] sm:grid-cols-2 lg:grid-cols-4"><label className="text-secondary">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label><label className="text-secondary">审查范围<select value={scope} onChange={event => setScope(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none">{scopes.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><div className="rounded-lg border border-default bg-surface px-3 py-2 text-secondary">模型端点：由税务后端模型池动态选择</div><label className="text-secondary">指导意见<input value={instruction} onChange={event => setInstruction(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none" /></label></div><div className="mt-4 flex justify-end"><button type="button" onClick={() => void runSingleReview()} disabled={isRunning} className="flex items-center gap-2 rounded-xl bg-[var(--color-brand)] px-5 py-2.5 font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动专项审查'}</button></div></div><ResultPanel title="专项审查结果" data={reviewResult} /></div>}

      {activeTab === 'health' && <div className="space-y-5"><div className="surface-card rounded-xl p-5"><div className="grid grid-cols-1 gap-4 text-[12px] sm:grid-cols-2 lg:grid-cols-4"><label className="text-secondary">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label><label className="text-secondary">体检档位<select value={healthProfile} onChange={event => setHealthProfile(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none"><option value="quick">快速</option><option value="standard">标准</option><option value="deep">深度</option></select></label><div className="rounded-lg border border-default bg-surface px-3 py-2 text-secondary">模型端点：由税务后端模型池动态选择</div><label className="text-secondary">指导意见<input value={healthInstruction} onChange={event => setHealthInstruction(event.target.value)} className="mt-1 w-full rounded-lg border border-default bg-surface px-3 py-2 text-primary focus:border-[var(--color-brand)] focus:outline-none" /></label></div><div className="mt-4 flex justify-end"><button type="button" onClick={() => void runHealth()} disabled={isRunning} className="flex items-center gap-2 rounded-xl bg-[var(--color-brand)] px-5 py-2.5 font-bold text-white transition-colors hover:bg-[var(--color-brand-hover)] disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动综合体检'}</button></div></div><ResultPanel title="综合体检结果" data={healthResult} /></div>}
    </div>
  );
}
