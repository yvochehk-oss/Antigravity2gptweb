import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Loader2,
  RefreshCw,
  Save,
  X,
  XCircle,
} from 'lucide-react';
import {
  ApiError,
  fetchProjectRagMap,
  fetchRagPendingContracts,
  fetchRagStatus,
  confirmRagPendingContractAndCreateParties,
  rejectRagPendingContract,
  saveProjectRagMap,
  syncRagBatch,
  syncRagType,
} from '../api';
import {
  ProjectItem,
  ProjectRagMapping,
  RagProjectCandidate,
  RagStatusResponse,
  RagSyncResult,
  RagSyncPendingContract,
  RagSyncType,
} from '../types';
import {
  canSaveRagMapping,
  canSyncRagMapping,
  activeTaxProjectId,
  mappingChanged,
  mappingMismatchWarning,
  operationErrorTitle,
  ragErrorMessage,
  resolveTaxProjectId,
} from '../ragMappingUi';

interface NewTaxRecordModalProps {
  isOpen: boolean;
  onClose: () => void;
  projects: ProjectItem[];
  projectId?: number;
  defaultProjectCode?: string;
  defaultProjectName?: string;
  onSyncCompleted?: () => void;
}

type OperationState = 'idle' | 'saving' | 'syncing' | 'success' | 'pending' | 'partial' | 'failed' | 'running';

const SYNC_OPTIONS: Array<{ type: RagSyncType; label: string; description: string }> = [
  { type: 'invoice', label: '发票', description: '进销项发票凭证' },
  { type: 'contract', label: '合同', description: '合同及履约资料' },
  { type: 'payment', label: '付款', description: '银行付款/资金凭证' },
  { type: 'tax_payment', label: '完税凭证', description: '申报与完税资料' },
];

function statusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case 'SUCCESS': return '成功';
    case 'PENDING_REVIEW': return '待人工复核';
    case 'PARTIAL': return '部分完成';
    case 'FAILED': return '失败';
    case 'RUNNING': return '同步中';
    default: return status || '未知状态';
  }
}

function resultState(results: RagSyncResult[]): Exclude<OperationState, 'idle' | 'saving' | 'syncing'> {
  if (results.length === 0 || results.every(result => result.status.toUpperCase() === 'FAILED')) return 'failed';
  if (results.some(result => result.status.toUpperCase() === 'RUNNING')) return 'running';
  if (results.some(result => result.status.toUpperCase() === 'PARTIAL' || result.errors.length > 0)) return 'partial';
  if (results.some(result => result.status.toUpperCase() === 'PENDING_REVIEW' || result.totalPending > 0)) return 'pending';
  return 'success';
}

function candidateLabel(candidate: RagProjectCandidate): string {
  const parts = [candidate.projectCode, candidate.name].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : `RAG 项目 #${candidate.id}`;
}

function projectLabel(project: ProjectItem): string {
  return [project.projectCode, project.name].filter(Boolean).join(' · ') || `Tax 项目 #${project.numericId}`;
}

export function NewTaxRecordModal({
  isOpen,
  onClose,
  projects,
  projectId,
  defaultProjectCode,
  defaultProjectName,
  onSyncCompleted,
}: NewTaxRecordModalProps) {
  const [selectedTaxProjectId, setSelectedTaxProjectId] = useState(() => resolveTaxProjectId(projects, projectId));
  const [ragStatus, setRagStatus] = useState<RagStatusResponse | null>(null);
  const [candidates, setCandidates] = useState<RagProjectCandidate[]>([]);
  const [mapping, setMapping] = useState<ProjectRagMapping | null>(null);
  const [mappingVerified, setMappingVerified] = useState(false);
  const [selectedRagProjectId, setSelectedRagProjectId] = useState('');
  const [selectedTypes, setSelectedTypes] = useState<RagSyncType[]>(['invoice', 'contract', 'payment', 'tax_payment']);
  const [operation, setOperation] = useState<OperationState>('idle');
  const [syncResults, setSyncResults] = useState<RagSyncResult[]>([]);
  const [isEditingMapping, setIsEditingMapping] = useState(false);
  const [pendingMappingCandidate, setPendingMappingCandidate] = useState<RagProjectCandidate | null>(null);
  const [mappingNotice, setMappingNotice] = useState('');
  const [statusError, setStatusError] = useState('');
  const [mappingError, setMappingError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [syncError, setSyncError] = useState('');
  const [pendingContracts, setPendingContracts] = useState<RagSyncPendingContract[]>([]);
  const [pendingContractsError, setPendingContractsError] = useState('');
  const [pendingContractsLoading, setPendingContractsLoading] = useState(false);
  const [confirmPendingId, setConfirmPendingId] = useState<number | null>(null);
  const [confirmingPendingId, setConfirmingPendingId] = useState<number | null>(null);
  const [confirmRejectPendingId, setConfirmRejectPendingId] = useState<number | null>(null);
  const [rejectingPendingId, setRejectingPendingId] = useState<number | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [mappingLoading, setMappingLoading] = useState(false);
  const [statusRetry, setStatusRetry] = useState(0);
  const [mappingRetry, setMappingRetry] = useState(0);
  const statusAbortRef = useRef<AbortController | null>(null);
  const mappingAbortRef = useRef<AbortController | null>(null);

  const selectedTaxProject = useMemo(
    () => projects.find(project => String(project.numericId) === selectedTaxProjectId),
    [projects, selectedTaxProjectId],
  );
  const activeProjectId = activeTaxProjectId(projects, selectedTaxProjectId, projectId);
  const activeProjectCode = selectedTaxProject?.projectCode || defaultProjectCode;
  const activeProjectName = selectedTaxProject?.name || defaultProjectName;
  const statusOk = ragStatus?.ok === true && !statusError;
  const selectedCandidate = useMemo(
    () => candidates.find(candidate => String(candidate.id) === selectedRagProjectId),
    [candidates, selectedRagProjectId],
  );
  const mappingCandidate = useMemo(
    () => mapping ? candidates.find(candidate => candidate.id === mapping.ragProjectId) : undefined,
    [candidates, mapping],
  );
  const busy = statusLoading || mappingLoading || operation === 'saving' || operation === 'syncing' || confirmingPendingId !== null || rejectingPendingId !== null;
  const canSave = canSaveRagMapping({
    statusOk,
    candidates,
    selected: selectedCandidate,
    mapping,
    editing: isEditingMapping,
    busy,
  });
  const canSync = canSyncRagMapping({
    statusOk,
    candidates,
    mapping,
    mappingCandidate,
    mappingVerified,
    editing: isEditingMapping,
    selectedTypes: selectedTypes.length,
    busy,
  });
  const serviceConnected = statusOk && !mappingLoading && !mappingError && !saveError && !syncError;

  useEffect(() => {
    if (!isOpen) return undefined;
    setSelectedTaxProjectId(current => resolveTaxProjectId(projects, projectId, current));
    return undefined;
  }, [isOpen, projectId, projects]);

  useEffect(() => {
    if (!isOpen) return undefined;
    statusAbortRef.current?.abort();
    const controller = new AbortController();
    statusAbortRef.current = controller;
    setStatusLoading(true);
    setStatusError('');
    setRagStatus(null);
    setCandidates([]);
    void fetchRagStatus(controller.signal)
      .then(status => {
        if (controller.signal.aborted) return;
        if (!status.ok) {
          setRagStatus(status);
          setCandidates([]);
          setStatusError(ragErrorMessage(new ApiError(status.error || 'RAG 服务返回不可用状态。', 0, status), 'RAG 状态读取失败。'));
          return;
        }
        setRagStatus(status);
        setCandidates(status.projects);
      })
      .catch(error => {
        if (!controller.signal.aborted) {
          setRagStatus(null);
          setCandidates([]);
          setStatusError(ragErrorMessage(error, 'RAG 状态读取失败。'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setStatusLoading(false);
      });
    return () => {
      controller.abort();
      if (statusAbortRef.current === controller) statusAbortRef.current = null;
    };
  }, [isOpen, statusRetry]);

  useEffect(() => {
    if (!isOpen) return undefined;
    mappingAbortRef.current?.abort();
    const controller = new AbortController();
    mappingAbortRef.current = controller;
    setMappingLoading(true);
    setMappingError('');
    setSaveError('');
    setSyncError('');
    setMapping(null);
    setMappingVerified(false);
    setSelectedRagProjectId('');
    setPendingMappingCandidate(null);
    setIsEditingMapping(false);
    setMappingNotice('');
    setOperation('idle');
    setSyncResults([]);
    if (!activeProjectId || !Number.isInteger(activeProjectId) || activeProjectId <= 0) {
      setMappingError('当前没有有效的 Tax 项目，无法读取项目映射。');
      setMappingLoading(false);
      return () => controller.abort();
    }
    void fetchProjectRagMap(activeProjectId, controller.signal)
      .then(currentMapping => {
        if (controller.signal.aborted) return;
        setMapping(currentMapping);
        setMappingVerified(Boolean(currentMapping));
        setSelectedRagProjectId(currentMapping ? String(currentMapping.ragProjectId) : '');
      })
      .catch(error => {
        if (!controller.signal.aborted) {
          setMapping(null);
          setMappingVerified(false);
          setMappingError(ragErrorMessage(error, 'Tax 项目映射读取失败。'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setMappingLoading(false);
      });
    return () => {
      controller.abort();
      if (mappingAbortRef.current === controller) mappingAbortRef.current = null;
    };
  }, [isOpen, activeProjectId, mappingRetry]);

  useEffect(() => () => {
    statusAbortRef.current?.abort();
    mappingAbortRef.current?.abort();
  }, []);

  const reloadPendingContracts = async () => {
    if (!activeProjectId) return;
    setPendingContractsLoading(true);
    setPendingContractsError('');
    try {
      setPendingContracts(await fetchRagPendingContracts(activeProjectId));
    } catch (error) {
      setPendingContracts([]);
      setPendingContractsError(ragErrorMessage(error, '待复核合同读取失败。'));
    } finally {
      setPendingContractsLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen || !activeProjectId) return undefined;
    void reloadPendingContracts();
    return undefined;
  // The request is deliberately refreshed only when the active Tax project changes or after an explicit action.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, activeProjectId]);

  if (!isOpen) return null;

  const currentMapping = mapping;
  const selectedCandidateWarning = mappingMismatchWarning(activeProjectCode, activeProjectName, selectedCandidate);
  const requestStatusRetry = () => setStatusRetry(value => value + 1);
  const requestMappingRetry = () => setMappingRetry(value => value + 1);

  const handleProjectChange = (value: string) => {
    if (busy) return;
    setSelectedTaxProjectId(value);
    setSelectedRagProjectId('');
    setPendingMappingCandidate(null);
    setMappingNotice('');
    setSaveError('');
    setSyncError('');
    setOperation('idle');
  };

  const toggleType = (type: RagSyncType) => {
    setSelectedTypes(current => current.includes(type)
      ? current.filter(item => item !== type)
      : [...current, type]);
    setSyncError('');
    setSyncResults([]);
    setOperation('idle');
  };

  const beginMappingEdit = () => {
    if (!currentMapping || !statusOk || candidates.length === 0 || busy) return;
    setIsEditingMapping(true);
    setSelectedRagProjectId('');
    setPendingMappingCandidate(null);
    setMappingNotice('');
    setSaveError('');
    setSyncError('');
    setOperation('idle');
  };

  const cancelMappingEdit = () => {
    if (busy) return;
    setIsEditingMapping(false);
    setPendingMappingCandidate(null);
    setSelectedRagProjectId(currentMapping ? String(currentMapping.ragProjectId) : '');
    setMappingNotice('');
    setSaveError('');
    setOperation('idle');
  };

  const requestSaveMapping = () => {
    if (!activeProjectId || !selectedCandidate || !canSave) {
      setSaveError('请选择 RAG 状态接口返回的真实候选；当前未保存任何映射。');
      return;
    }
    if (!mappingChanged(currentMapping, selectedCandidate)) {
      setSaveError('请选择与当前映射不同的真实 RAG 候选；当前映射未改变。');
      return;
    }
    setPendingMappingCandidate(selectedCandidate);
    setSaveError('');
    setMappingNotice('');
  };

  const handleConfirmSaveMapping = async () => {
    const candidate = pendingMappingCandidate;
    if (!activeProjectId || !candidate || !statusOk || busy) return;
    setOperation('saving');
    setSaveError('');
    setSyncError('');
    setMappingVerified(false);
    setPendingMappingCandidate(null);
    try {
      await saveProjectRagMap({
        projectId: activeProjectId,
        ragProjectId: candidate.id,
        ragProjectCode: candidate.projectCode,
      });
      const savedMapping = await fetchProjectRagMap(activeProjectId);
      if (!savedMapping || savedMapping.projectId !== activeProjectId || savedMapping.ragProjectId !== candidate.id) {
        throw new ApiError('映射保存后重新读取结果不匹配，已停止后续同步。');
      }
      setMapping(savedMapping);
      setSelectedRagProjectId(String(savedMapping.ragProjectId));
      setMappingVerified(true);
      setIsEditingMapping(false);
      setOperation('success');
      setMappingNotice('映射已保存并重新读取确认；本次仅更新映射，未自动触发 RAG 同步。');
    } catch (error) {
      setOperation('failed');
      setSaveError(ragErrorMessage(error, 'RAG 映射保存失败。'));
    }
  };

  const handleSync = async () => {
    if (!activeProjectId || !currentMapping || !canSync) return;
    setOperation('syncing');
    setSyncError('');
    setSyncResults([]);
    try {
      const results = selectedTypes.length === 1
        ? [await syncRagType({ projectId: activeProjectId, ragProjectId: currentMapping.ragProjectId, syncType: selectedTypes[0] })]
        : (await syncRagBatch({ projectId: activeProjectId, ragProjectId: currentMapping.ragProjectId, syncTypes: selectedTypes })).results;
      setSyncResults(results);
      setOperation(resultState(results));
      await reloadPendingContracts();
      onSyncCompleted?.();
    } catch (error) {
      setOperation('failed');
      setSyncError(ragErrorMessage(error, 'RAG 凭证同步失败。'));
    }
  };

  const handleConfirmPendingContract = async (pendingId: number) => {
    if (confirmingPendingId !== null) return;
    setConfirmingPendingId(pendingId);
    setPendingContractsError('');
    try {
      const result = await confirmRagPendingContractAndCreateParties(pendingId);
      setConfirmPendingId(null);
      await reloadPendingContracts();
      const created = result.createdExternalParties.length;
      setMappingNotice(`合同已导入（记录 #${result.recordId}）；${created > 0 ? `已创建 ${created} 个外部交易方主数据。` : '交易方主数据已存在。'}`);
      onSyncCompleted?.();
    } catch (error) {
      setPendingContractsError(ragErrorMessage(error, '确认合同并创建交易方失败。'));
    } finally {
      setConfirmingPendingId(null);
    }
  };

  const handleRejectPendingContract = async (pendingId: number) => {
    if (rejectingPendingId !== null) return;
    setRejectingPendingId(pendingId);
    setPendingContractsError('');
    try {
      await rejectRagPendingContract(pendingId);
      setConfirmRejectPendingId(null);
      await reloadPendingContracts();
      setMappingNotice(`已忽略待复核记录 #${pendingId}。`);
      onSyncCompleted?.();
    } catch (error) {
      setPendingContractsError(ragErrorMessage(error, '忽略待复核记录失败。'));
    } finally {
      setRejectingPendingId(null);
    }
  };

  const totalExtracted = syncResults.reduce((sum, result) => sum + result.totalExtracted, 0);
  const totalImported = syncResults.reduce((sum, result) => sum + result.totalImported, 0);
  const totalPending = syncResults.reduce((sum, result) => sum + result.totalPending, 0);
  const resultTone = operation === 'success' || operation === 'running'
    ? 'border-[#10B981]/40 bg-[#10B981]/10 text-[#b6f4d8]'
    : operation === 'pending' || operation === 'partial'
      ? 'border-[#F59E0B]/40 bg-[#F59E0B]/10 text-[#ffd0a8]'
      : 'border-[#EF4444]/40 bg-[#EF4444]/10 text-[#ffb4ab]';

  return (
    <div className="fixed inset-0 bg-black/75 backdrop-blur-md z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="rag-sync-title">
      <div className="bg-[#131b2e] border border-[#4cd7f6]/40 rounded-2xl max-w-2xl w-full p-6 shadow-2xl text-[#dae2fd] max-h-[92vh] overflow-y-auto">
        <div className="flex justify-between items-start pb-4 border-b border-[#444653]/30">
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-[#4cd7f6]" />
            <div>
              <h3 id="rag-sync-title" className="text-[18px] font-bold">RAG 凭证同步</h3>
              <p className="text-[12px] text-[#8e909f] mt-0.5">当前展示 Tax 服务返回的确定性税务台账；RAG 凭证同步用于补充原始凭证、证据和待复核信息。</p>
            </div>
          </div>
          <button type="button" onClick={onClose} disabled={busy} className="text-[#8e909f] hover:text-[#dae2fd] disabled:opacity-40" aria-label="关闭"><X className="w-5 h-5" /></button>
        </div>

        <label className="block mt-5 text-[12px] text-[#c4c5d5]">
          Tax 项目
          <select aria-label="选择 Tax 项目" value={selectedTaxProjectId} onChange={event => handleProjectChange(event.target.value)} disabled={busy || projects.length === 0} className="mt-1.5 w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[13px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none disabled:opacity-50">
            <option value="">请选择 Tax 项目</option>
            {projects.map(project => <option key={project.numericId} value={project.numericId}>{projectLabel(project)}</option>)}
          </select>
        </label>
        <p className="text-[12px] text-[#8e909f] mt-2">当前项目：{activeProjectCode || (activeProjectId ? `Tax 项目 #${activeProjectId}` : '未选择项目')} · {activeProjectName || '未提供项目名称'}</p>

        {(statusLoading || mappingLoading) && <div className="mt-4 rounded-xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-3 flex items-center gap-2" role="status"><Loader2 className="w-4 h-4 animate-spin text-[#4cd7f6]" />正在读取真实 RAG 状态和当前项目映射…</div>}

        {statusError && <div className="mt-4 rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/10 p-3" role="alert"><p className="font-semibold text-[#ffb4ab]"><XCircle className="inline-block w-4 h-4 mr-1 align-[-2px]" />{operationErrorTitle('status')}</p><p className="text-[12px] mt-1">{statusError}</p><button type="button" onClick={requestStatusRetry} disabled={busy} className="mt-2 inline-flex items-center gap-1 rounded-lg bg-[#222a3d] px-2.5 py-1.5 text-[12px] disabled:opacity-40"><RefreshCw className="w-3.5 h-3.5" />重新读取 RAG 状态</button></div>}
        {mappingError && <div className="mt-4 rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/10 p-3" role="alert"><p className="font-semibold text-[#ffb4ab]"><XCircle className="inline-block w-4 h-4 mr-1 align-[-2px]" />{operationErrorTitle('mapping')}</p><p className="text-[12px] mt-1">{mappingError}</p><button type="button" onClick={requestMappingRetry} disabled={busy} className="mt-2 inline-flex items-center gap-1 rounded-lg bg-[#222a3d] px-2.5 py-1.5 text-[12px] disabled:opacity-40"><RefreshCw className="w-3.5 h-3.5" />重新读取当前项目映射</button></div>}
        {saveError && <div className="mt-4 rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/10 p-3" role="alert"><p className="font-semibold text-[#ffb4ab]"><XCircle className="inline-block w-4 h-4 mr-1 align-[-2px]" />{operationErrorTitle('save')}</p><p className="text-[12px] mt-1">{saveError}</p></div>}
        {syncError && <div className="mt-4 rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/10 p-3" role="alert"><p className="font-semibold text-[#ffb4ab]"><XCircle className="inline-block w-4 h-4 mr-1 align-[-2px]" />{operationErrorTitle('sync')}</p><p className="text-[12px] mt-1">{syncError}</p></div>}

        {serviceConnected && <div className="mt-4 rounded-xl border border-[#10B981]/30 bg-[#10B981]/5 p-3 flex items-start gap-2"><CheckCircle2 className="w-5 h-5 text-[#10B981] flex-shrink-0" /><div className="text-[12px]"><p className="font-semibold text-[#b6f4d8]">RAG 服务已连接</p><p className="text-[#c4c5d5] mt-1">版本：{ragStatus?.ragVersion || '后端未提供'} · LLM 抽取：{ragStatus?.llmExtraction ? '已启用' : '未启用'} · 候选项目：{candidates.length}</p><p className="text-[11px] text-[#8e909f] mt-1">候选仅来自 /rag-sync/status 的真实响应；页面不显示、不接收 API key。</p></div></div>}

        {statusOk && candidates.length === 0 && <div className="mt-4 rounded-xl border border-[#F59E0B]/30 bg-[#F59E0B]/5 p-3 text-[12px] text-[#ffd0a8]">RAG 状态可读取，但未返回真实项目候选。保存、重新配置和同步均已安全禁用，当前映射不会被覆盖。</div>}

        {statusOk && !mappingError && !mappingLoading && (currentMapping ? isEditingMapping : true) && (
          <div className="mt-4 rounded-xl border border-[#F59E0B]/30 bg-[#F59E0B]/5 p-4">
            <div className="flex items-start gap-2"><AlertTriangle className="w-5 h-5 text-[#F59E0B] flex-shrink-0" /><div className="min-w-0 flex-1"><p className="font-semibold">{currentMapping ? '重新配置 Tax ↔ RAG 项目映射' : '尚未配置 Tax ↔ RAG 项目映射'}</p><p className="text-[12px] text-[#c4c5d5] mt-1.5">系统只展示 RAG 后端真实返回的候选，不会根据名称、编号或顺序自动猜测映射。</p>
              {candidates.length > 0 && <><label className="block text-[12px] text-[#c4c5d5] mt-3">RAG 项目候选<select aria-label="选择 RAG 项目" value={selectedRagProjectId} onChange={event => { setSelectedRagProjectId(event.target.value); setPendingMappingCandidate(null); setSaveError(''); }} disabled={busy} className="mt-1.5 w-full bg-[#131b2e] border border-[#444653]/50 rounded-lg px-3 py-2 text-[13px] text-[#dae2fd] disabled:opacity-50"><option value="">请选择后端返回的真实 RAG 项目</option>{candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidateLabel(candidate)} · ID {candidate.id}</option>)}</select></label>{selectedCandidate && <div className="mt-2 text-[12px] text-[#c4c5d5]">准备保存：{candidateLabel(selectedCandidate)} · ID {selectedCandidate.id}{selectedCandidateWarning && <p className="mt-1 text-[#ffd0a8]">{selectedCandidateWarning}</p>}</div>}
                {!pendingMappingCandidate ? <button type="button" onClick={() => { if (activeProjectId && selectedCandidate && mappingChanged(currentMapping, selectedCandidate)) setPendingMappingCandidate(selectedCandidate); else setSaveError('请选择与当前映射不同的真实 RAG 候选；当前映射未改变。'); }} disabled={!canSave} className="mt-3 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#1e40af] text-[12px] font-semibold disabled:opacity-40"><Save className="w-3.5 h-3.5" />准备保存映射</button> : <div className="mt-3 rounded-lg border border-[#EF4444]/50 bg-[#EF4444]/10 p-3" role="alert"><p className="font-semibold text-[#ffb4ab]">请二次确认覆盖映射</p><p className="text-[12px] mt-1.5">将当前 Tax 项目映射到 {candidateLabel(pendingMappingCandidate)}（ID {pendingMappingCandidate.id}）。确认后只覆盖映射，不会自动同步凭证。</p><div className="mt-2 flex gap-2"><button type="button" onClick={() => setPendingMappingCandidate(null)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">取消确认</button><button type="button" onClick={() => void handleConfirmSaveMapping()} disabled={busy} className="px-3 py-1.5 rounded-lg bg-[#EF4444] text-white text-[12px] font-bold disabled:opacity-40">{operation === 'saving' ? '保存中…' : '确认覆盖保存'}</button></div></div>}
              </>}
              {currentMapping && <button type="button" onClick={cancelMappingEdit} disabled={busy} className="ml-2 mt-3 px-4 py-2 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">取消并保留当前映射</button>}
            </div></div>
          </div>
        )}

        {statusOk && !mappingError && !mappingLoading && currentMapping && !isEditingMapping && <div className="mt-4 rounded-xl border border-[#4cd7f6]/30 bg-[#03b5d3]/5 p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-[#4cd7f6]">当前已映射 RAG 知识空间</p><p className="text-[13px] mt-1">{currentMapping.ragProjectCode || `RAG 项目 #${currentMapping.ragProjectId}`} · {mappingCandidate?.name || 'RAG 后端未返回名称'} · ID {currentMapping.ragProjectId}</p><p className="text-[11px] text-[#8e909f] mt-1">映射已由当前项目的 project-map 接口读取确认。</p></div><button type="button" onClick={beginMappingEdit} disabled={!statusOk || candidates.length === 0 || busy} className="px-3 py-1.5 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">重新配置映射</button></div>{mappingNotice && <p className="mt-2 text-[12px] text-[#b6f4d8]" role="status"><CheckCircle2 className="inline-block w-3.5 h-3.5 mr-1" />{mappingNotice}</p>}{!mappingCandidate && <p className="mt-2 text-[12px] text-[#ffd0a8]">当前映射不在最新 RAG 候选列表中，已禁用同步，请先重新确认映射。</p>}</div>}

        {statusOk && !mappingError && !mappingLoading && currentMapping && !isEditingMapping && <div className="mt-4 rounded-xl border border-[#444653]/40 bg-[#0b1326]/50 p-4"><p className="font-semibold text-[14px]">选择同步类型</p><div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-3">{SYNC_OPTIONS.map(option => { const checked = selectedTypes.includes(option.type); return <label key={option.type} className={`flex items-start gap-2.5 rounded-lg border p-3 ${checked ? 'border-[#4cd7f6]/60 bg-[#03b5d3]/10' : 'border-[#444653]/40 bg-[#131b2e]'}`}><input type="checkbox" checked={checked} onChange={() => toggleType(option.type)} disabled={busy || !canSync} className="mt-0.5 accent-[#4cd7f6]" /><span><span className="block text-[13px] font-semibold">{option.label}</span><span className="block text-[11px] text-[#8e909f] mt-0.5">{option.description}</span></span></label>; })}</div><div className="mt-3 flex justify-end"><button type="button" onClick={() => void handleSync()} disabled={!canSync} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#03b5d3] text-[#001f26] text-[12px] font-bold disabled:opacity-40">{operation === 'syncing' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}{operation === 'syncing' ? '同步执行中…' : selectedTypes.length > 1 ? '开始批量同步' : '开始单类同步'}</button></div></div>}

        {activeProjectId && <div className="mt-4 rounded-xl border border-[#F59E0B]/35 bg-[#F59E0B]/5 p-4">
          <div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-[14px] text-[#ffd0a8]">待复核合同</p><p className="text-[12px] text-[#c4c5d5] mt-1">只有管理员二次确认后，系统才会按 Tax 保存的抽取结果创建缺失交易方主数据并导入合同。</p></div><button type="button" onClick={() => void reloadPendingContracts()} disabled={pendingContractsLoading || busy} className="px-2.5 py-1.5 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">{pendingContractsLoading ? '读取中…' : '刷新'}</button></div>
          {pendingContractsError && <p className="mt-2 text-[12px] text-[#ffb4ab]" role="alert">{pendingContractsError}</p>}
          {!pendingContractsLoading && !pendingContractsError && pendingContracts.length === 0 && <p className="mt-2 text-[12px] text-[#8e909f]">当前项目没有待复核合同。</p>}
          <div className="mt-3 space-y-3">{pendingContracts.map(item => {
            const hasAnyParty = Boolean(item.partyA.name || item.partyA.taxId || item.partyB.name || item.partyB.taxId);
            return (
              <div key={item.id} className="rounded-lg border border-[#F59E0B]/25 bg-[#131b2e] p-3 text-[12px]">
                <div className="flex justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold break-all">{item.filename}</p>
                    <p className="text-[#8e909f] mt-1">来源 chunk #{item.sourceChunkId}{item.pageStart ? ` · 第 ${item.pageStart} 页` : ''} · 置信度 {(item.confidence * 100).toFixed(1)}%</p>
                  </div>
                </div>
                <p className="mt-2 text-[#ffd0a8]">{item.reason}</p>
                {!hasAnyParty && (
                  <p className="mt-2 text-[11px] text-[#fca5a5] bg-[#7f1d1d]/30 border border-[#f87171]/40 rounded px-2.5 py-1.5">
                    ℹ️ 此文件为附件/扫描件（无甲乙双方主体与税号信息），无法作为独立合同台账入库，请点击【忽略此记录】。
                  </p>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2 text-[#c4c5d5]">
                  <p><span className="text-[#8e909f]">甲方：</span>{item.partyA.name || '未提供名称'}<br /><span className="text-[#8e909f]">税号：</span>{item.partyA.taxId || '未提供'}</p>
                  <p><span className="text-[#8e909f]">乙方：</span>{item.partyB.name || '未提供名称'}<br /><span className="text-[#8e909f]">税号：</span>{item.partyB.taxId || '未提供'}</p>
                </div>
                {confirmPendingId === item.id ? (
                  <div className="mt-3 rounded-lg border border-[#EF4444]/50 bg-[#EF4444]/10 p-3">
                    <p className="font-semibold text-[#ffb4ab]">旧手工创建入口已停用</p>
                    <p className="mt-1 text-[#c4c5d5]">该记录只能继续复核或忽略；交易方身份与合同事实统一由 Canonical Facts 自动归一化。</p>
                    <div className="mt-2 flex gap-2">
                      <button type="button" onClick={() => setConfirmPendingId(null)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">取消</button>
                      <span className="px-3 py-1.5 rounded-lg bg-[#334155]/50 text-[#cbd5e1] text-[12px]">已停用</span>
                    </div>
                  </div>
                ) : confirmRejectPendingId === item.id ? (
                  <div className="mt-3 rounded-lg border border-[#64748b]/50 bg-[#334155]/20 p-3">
                    <p className="font-semibold text-[#cbd5e1]">确认忽略此待复核记录？</p>
                    <p className="mt-1 text-[#94a3b8]">忽略后此记录将标记为 rejected，不再提示待复核，也不会录入合同台账。</p>
                    <div className="mt-2 flex gap-2">
                      <button type="button" onClick={() => setConfirmRejectPendingId(null)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-[#222a3d] text-[12px] disabled:opacity-40">取消</button>
                      <button type="button" onClick={() => void handleRejectPendingContract(item.id)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-[#475569] text-white font-bold text-[12px] disabled:opacity-40">{rejectingPendingId === item.id ? '处理中…' : '确认忽略'}</button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {hasAnyParty && (
                      <span className="px-3 py-1.5 rounded-lg border border-[#10B981]/30 bg-[#10B981]/10 text-[#b6f4d8] text-[12px]">Canonical Facts 自动直通，无需手工创建交易方</span>
                    )}
                    <button type="button" onClick={() => { setConfirmRejectPendingId(item.id); setConfirmPendingId(null); }} disabled={busy} className={`px-3 py-1.5 rounded-lg text-[12px] disabled:opacity-40 transition-colors ${!hasAnyParty ? 'bg-[#3b82f6] text-white font-bold hover:bg-[#2563eb]' : 'bg-[#222a3d] border border-[#444653]/60 text-[#c4c5d5] hover:text-white'}`}>忽略此记录</button>
                  </div>
                )}
              </div>
            );
          })}</div>
        </div>}

        {syncResults.length > 0 && <div className={`mt-4 rounded-xl border p-4 ${resultTone}`} role="status"><p className="font-semibold">同步结果：{statusLabel(operation === 'pending' ? 'PENDING_REVIEW' : operation === 'partial' ? 'PARTIAL' : operation === 'success' ? 'SUCCESS' : operation === 'running' ? 'RUNNING' : 'FAILED')}</p><div className="grid grid-cols-3 gap-2 mt-3 text-[12px]"><div><span className="block opacity-70">抽取</span><strong>{totalExtracted}</strong></div><div><span className="block opacity-70">已导入</span><strong>{totalImported}</strong></div><div><span className="block opacity-70">待复核</span><strong>{totalPending}</strong></div></div><div className="mt-3 space-y-2">{syncResults.map(result => <div key={`${result.syncType}-${result.syncLogId}`} className="rounded-lg border border-current/20 bg-black/10 p-2.5 text-[12px]"><div className="flex justify-between gap-2"><span>{SYNC_OPTIONS.find(option => option.type === result.syncType)?.label || result.syncType}</span><span>{statusLabel(result.status)}</span></div>{result.errors.length > 0 && <ul className="mt-1.5 list-disc list-inside">{result.errors.map((item, index) => <li key={`${result.syncLogId}-${index}`}>{item}</li>)}</ul>}</div>)}</div></div>}

        <div className="flex justify-end mt-5 pt-4 border-t border-[#444653]/30"><button type="button" onClick={onClose} disabled={busy} className="px-4 py-2 rounded-lg bg-[#222a3d] text-[#dae2fd] disabled:opacity-40">关闭</button></div>
      </div>
    </div>
  );
}
