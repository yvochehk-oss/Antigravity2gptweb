import { useEffect, useState } from 'react';
import { BrainCircuit, RefreshCw, Stethoscope } from 'lucide-react';
import { ApiError, extractAiExecutionMetadata, runAiReview, runHealthCheck } from '../api';
import { AiExecutionMetadata, DataStatus, ProjectItem } from '../types';
import { DataStatusCard } from './DataStatusCard';

interface AiReviewViewProps {
  projects?: ProjectItem[];
  dataStatus: DataStatus;
  onAskAiAboutRisk?: (entityName: string) => void;
}

const scopes = [
  ['overview', '项目总体经营'], ['budget', '预算与成本偏差'], ['contract', '合同'], ['fulfillment', '履约证据'],
  ['invoice', '发票'], ['cashflow', '资金与付款'], ['cost', '真实成本穿透'], ['tax', '税务管理测算'],
  ['eac', 'EAC预计完工'], ['risk', '风险事件'], ['material', '材料'], ['labor', '劳务'],
  ['equipment', '设备'], ['subcontract', '专业分包'], ['whole_project', '整个项目'],
] as const;

type ApiResult = Record<string, unknown>;

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function endpointLabel(endpoint: AiExecutionMetadata['effectiveEndpoint']): string {
  if (!endpoint) return '—';
  return [endpoint.id !== undefined ? `#${endpoint.id}` : '', endpoint.name ?? '', endpoint.model ?? '']
    .filter(Boolean)
    .join(' · ') || '—';
}

function ExecutionMetadataPanel({ data }: { data: ApiResult }) {
  const metadata = extractAiExecutionMetadata(data);
  const attempts = Array.isArray(metadata.attempts) ? metadata.attempts : [];
  if (Object.keys(metadata).length === 0) return null;
  return (
    <div className="glass-panel rounded-xl p-4 border border-[#F59E0B]/30 text-[12px]">
      <h4 className="font-bold text-[#dae2fd]">模型执行信息（后端返回）</h4>
      <div className="flex flex-wrap gap-x-4 gap-y-2 mt-3 text-[#c4c5d5]">
        {(metadata.selectedEndpoint || metadata.effectiveEndpoint) && <span>实际端点：{endpointLabel(metadata.effectiveEndpoint ?? metadata.selectedEndpoint)}</span>}
        {metadata.status && <span>状态：{metadata.status}</span>}
        {metadata.fallback !== undefined && <span>fallback：{metadata.fallback ? '是' : '否'}</span>}
        {metadata.degraded !== undefined && <span>DEGRADED：{metadata.degraded ? '是' : '否'}</span>}
      </div>
      {attempts.length > 0 && <div className="mt-2 text-[#aeb5ca]">尝试摘要：{attempts.map((attempt, index) => `${index + 1}. ${endpointLabel(attempt.endpoint)}${attempt.status ? `/${attempt.status}` : ''}${attempt.error ? `：${attempt.error}` : ''}`).join('；')}</div>}
    </div>
  );
}

function ResultPanel({ title, data }: { title: string; data: ApiResult | null }) {
  if (!data) return <DataStatusCard status="UNAVAILABLE" title={`${title}尚无结果`} message="执行后端任务后，真实结果会显示在这里。" />;
  const result = (data.result && typeof data.result === 'object' ? data.result : data.consensus && typeof data.consensus === 'object' ? data.consensus : data) as ApiResult;
  const arrayEntries = Object.entries(result).filter(([, value]) => Array.isArray(value));
  const scalarEntries = Object.entries(result).filter(([, value]) => !Array.isArray(value) && typeof value !== 'object');
  return (
    <div className="space-y-4">
      <ExecutionMetadataPanel data={data} />
      <div className="glass-panel rounded-xl p-5 border border-[#4cd7f6]/30">
        <h3 className="text-[15px] font-bold text-[#dae2fd]">{title}（真实 API 返回）</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-[12px]">
          {scalarEntries.map(([key, value]) => <div key={key} className="rounded-lg bg-[#131b2e] p-3"><p className="text-[#8e909f]">{key}</p><p className="text-[#dae2fd] font-semibold mt-1 break-all">{renderValue(value)}</p></div>)}
        </div>
      </div>
      {arrayEntries.map(([key, value]) => <div key={key} className="glass-panel rounded-xl p-5 border border-[#444653]/30"><h4 className="text-[14px] font-bold text-[#dae2fd] mb-3">{key}</h4><div className="space-y-2">{(value as unknown[]).map((item, index) => <pre key={index} className="rounded-lg bg-[#131b2e] border border-[#444653]/20 p-3 text-[11px] text-[#c4c5d5] whitespace-pre-wrap overflow-auto">{JSON.stringify(item, null, 2)}</pre>)}</div></div>)}
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
  const [statusMessage, setStatusMessage] = useState('尚未执行真实 AI 任务。');
  const [status, setStatus] = useState<DataStatus>(dataStatus === 'READY' ? 'READY' : dataStatus);

  useEffect(() => {
    setProjectId(projects[0]?.id ?? '');
    setReviewResult(null);
    setHealthResult(null);
  }, [projects]);

  useEffect(() => {
    setStatus(dataStatus);
    if (dataStatus !== 'READY') setStatusMessage('没有真实项目数据，AI 任务不可启动。');
  }, [dataStatus]);

  const runSingleReview = async () => {
    const project = projects.find(item => item.id === projectId);
    if (!project) {
      setStatus('DEGRADED');
      setStatusMessage('请选择真实项目。');
      return;
    }
    setIsRunning(true);
    setStatusMessage('正在执行 AI 专项审查…');
    try {
      const data = await runAiReview({ projectId: project.numericId, scope, instruction });
      setReviewResult(data);
      const resultStatus = typeof data.status === 'string' ? data.status : 'READY';
      setStatus(resultStatus === 'DEGRADED' || resultStatus === 'UNAVAILABLE' ? resultStatus : 'READY');
      setStatusMessage(resultStatus === 'READY' ? '专项审查结果来自 Tax AI Review API。' : `专项审查返回 ${resultStatus}，请结合端点执行信息人工复核。`);
    } catch (error) {
      setReviewResult(null);
      setStatus('DEGRADED');
      setStatusMessage(error instanceof ApiError ? error.message : '专项审查接口调用失败。');
    } finally {
      setIsRunning(false);
    }
  };

  const runHealth = async () => {
    const project = projects.find(item => item.id === projectId);
    if (!project) {
      setStatus('DEGRADED');
      setStatusMessage('请选择真实项目。');
      return;
    }
    setIsRunning(true);
    setStatusMessage('正在执行多模型 AI 体检…');
    try {
      const data = await runHealthCheck({ projectId: project.numericId, profile: healthProfile, instruction: healthInstruction });
      setHealthResult(data);
      const resultStatus = typeof data.status === 'string' ? data.status : 'READY';
      setStatus(resultStatus === 'DEGRADED' || resultStatus === 'UNAVAILABLE' ? resultStatus : 'READY');
      setStatusMessage(resultStatus === 'READY' ? '体检结果来自 Tax Health Check API。' : `体检返回 ${resultStatus}，请结合端点执行信息人工复核。`);
    } catch (error) {
      setHealthResult(null);
      setStatus('DEGRADED');
      setStatusMessage(error instanceof ApiError ? error.message : 'AI 体检接口调用失败。');
    } finally {
      setIsRunning(false);
    }
  };

  if (dataStatus !== 'READY' || projects.length === 0) {
    return (
      <div className="space-y-6">
        <h2 className="text-[26px] font-bold text-[#dae2fd]">AI 审查与体检</h2>
        <DataStatusCard status={dataStatus} title="AI 审查不可用" message="请先加载真实项目数据。系统不会使用项目、风险或模型的本地演示数据。" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[26px] font-bold text-[#dae2fd] flex items-center gap-2"><BrainCircuit className="w-7 h-7 text-[#4cd7f6]" />AI 审查与多模型体检</h2>
        <p className="text-[13px] text-[#8e909f] mt-1">AI 只解释真实 Facts/确定性计算结果；接口失败时保留失败状态，不展示模拟结论。</p>
      </div>
      <div className="flex gap-2 p-1 bg-[#131b2e] rounded-xl border border-[#444653]/40 w-fit"><button type="button" onClick={() => setActiveTab('single')} className={`px-4 py-2 rounded-lg text-[13px] font-semibold ${activeTab === 'single' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}><BrainCircuit className="w-4 h-4 inline mr-1" />专项审查</button><button type="button" onClick={() => setActiveTab('health')} className={`px-4 py-2 rounded-lg text-[13px] font-semibold ${activeTab === 'health' ? 'bg-[#1e40af] text-[#dde1ff]' : 'text-[#8e909f]'}`}><Stethoscope className="w-4 h-4 inline mr-1" />综合体检</button></div>
      <DataStatusCard status={status} title="AI 服务状态" message={statusMessage} />

      {activeTab === 'single' && <div className="space-y-5">
        <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-[12px]">
            <label className="text-[#8e909f]">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label>
            <label className="text-[#8e909f]">审查范围<select value={scope} onChange={event => setScope(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]">{scopes.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
            <div className="text-[#8e909f] rounded-lg bg-[#131b2e] border border-[#444653]/30 px-3 py-2">模型端点：由 Tax 后端模型池动态选择</div>
            <label className="text-[#8e909f]">指导意见<input value={instruction} onChange={event => setInstruction(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]" /></label>
          </div>
          <div className="mt-4 flex justify-end"><button type="button" onClick={() => void runSingleReview()} disabled={isRunning} className="px-5 py-2.5 rounded-xl bg-[#03b5d3] text-[#001f26] font-bold disabled:opacity-50 flex items-center gap-2"><RefreshCw className={`w-4 h-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动专项审查'}</button></div>
        </div>
        <ResultPanel title="专项审查结果" data={reviewResult} />
      </div>}

      {activeTab === 'health' && <div className="space-y-5">
        <div className="glass-panel rounded-xl p-5 border border-[#444653]/40">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-[12px]">
            <label className="text-[#8e909f]">工程项目<select value={projectId} onChange={event => setProjectId(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]">{projects.map(project => <option key={project.id} value={project.id}>{project.projectCode} · {project.name}</option>)}</select></label>
            <label className="text-[#8e909f]">体检档位<select value={healthProfile} onChange={event => setHealthProfile(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]"><option value="quick">快速</option><option value="standard">标准</option><option value="deep">深度</option></select></label>
            <div className="text-[#8e909f] rounded-lg bg-[#131b2e] border border-[#444653]/30 px-3 py-2">模型端点：由 Tax 后端模型池动态选择</div>
            <label className="text-[#8e909f]">指导意见<input value={healthInstruction} onChange={event => setHealthInstruction(event.target.value)} className="mt-1 w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg px-3 py-2 text-[#dae2fd]" /></label>
          </div>
          <div className="mt-4 flex justify-end"><button type="button" onClick={() => void runHealth()} disabled={isRunning} className="px-5 py-2.5 rounded-xl bg-[#10B981] text-[#002114] font-bold disabled:opacity-50 flex items-center gap-2"><RefreshCw className={`w-4 h-4 ${isRunning ? 'animate-spin' : ''}`} />{isRunning ? '执行中…' : '启动综合体检'}</button></div>
        </div>
        <ResultPanel title="综合体检结果" data={healthResult} />
      </div>}
    </div>
  );
}
