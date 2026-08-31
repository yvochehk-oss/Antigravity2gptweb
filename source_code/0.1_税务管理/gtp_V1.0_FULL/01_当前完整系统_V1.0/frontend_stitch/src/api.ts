import {
  AiAttemptSummary,
  AiEndpointMetadata,
  AiExecutionMetadata,
  AiModelEndpointHealth,
  AiModelStatus,
  MatchingCompletenessStatus,
  MatchingCompletenessSummary,
  MatchingFlowCount,
  ProjectMatchingCompleteness,
  ProjectItem,
  ProjectRagMapping,
  RagProjectCandidate,
  RagServiceSettings,
  RagStatusResponse,
  RagSyncBatchResponse,
  RagConfirmPendingContractResult,
  RagConfirmedExternalParty,
  RagSyncPendingContract,
  RagSyncResult,
  RagSyncType,
  RiskEvent,
  TaxLedgerRecord,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly payload?: unknown;

  constructor(message: string, status = 0, payload?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

type ApiInit = RequestInit & { signal?: AbortSignal };

async function readPayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function payloadMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) return payload;
  if (payload && typeof payload === 'object') {
    const data = payload as Record<string, unknown>;
    const detail = data.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object') {
      const nested = detail as Record<string, unknown>;
      if (typeof nested.message === 'string') return nested.message;
      if (typeof nested.error === 'string') return nested.error;
    }
    if (typeof data.message === 'string' && data.message.trim()) return data.message;
    if (typeof data.error === 'string' && data.error.trim()) return data.error;
  }
  return fallback;
}

export async function fetchJson<T>(path: string, init: ApiInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...init,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('无法连接 Tax 服务，请确认后端已启动。');
  }

  const payload = await readPayload(response);
  if (!response.ok) {
    throw new ApiError(
      payloadMessage(payload, `Tax 服务返回 HTTP ${response.status}`),
      response.status,
      payload,
    );
  }
  return payload as T;
}

function parseAiModelEndpointHealth(value: unknown): AiModelEndpointHealth | null {
  const data = asRecord(value);
  if (!data || typeof data.name !== 'string' || !data.name.trim()) return null;
  const status = typeof data.status === 'string' ? data.status.trim().toLowerCase() : '';
  if (status !== 'ok' && status !== 'degraded' && status !== 'down') return null;
  return { name: data.name.trim(), status };
}

/**
 * Convert the Tax `/healthz` AI component into a fail-closed UI state.
 *
 * The backend probes the configured model pool itself. The browser only
 * presents that result and never treats a configured endpoint as healthy.
 */
export function parseAiModelStatus(payload: unknown): AiModelStatus {
  const root = asRecord(payload);
  const components = asRecord(root?.components);
  const ai = asRecord(components?.ai);
  const backendStatus = typeof ai?.status === 'string' ? ai.status.trim().toLowerCase() : '';
  if (backendStatus !== 'ok' && backendStatus !== 'degraded' && backendStatus !== 'down') {
    throw new ApiError('AI 健康状态接口返回格式不完整。', 502, payload);
  }
  const rawEndpoints = Array.isArray(ai?.endpoints) ? ai.endpoints : [];
  const parsedEndpoints = rawEndpoints.map(parseAiModelEndpointHealth);
  const hasMalformedEndpoint = parsedEndpoints.some(endpoint => endpoint === null);
  const endpoints = parsedEndpoints.filter((item): item is AiModelEndpointHealth => item !== null);
  const hasHealthyEndpoint = endpoints.some(endpoint => endpoint.status === 'ok');
  if (backendStatus === 'ok' && !hasMalformedEndpoint && endpoints.length > 0 && endpoints.every(endpoint => endpoint.status === 'ok')) {
    return { state: 'READY', message: 'AI 模型已就绪', endpoints };
  }
  if (backendStatus === 'degraded' && hasHealthyEndpoint) {
    return { state: 'DEGRADED', message: 'AI 模型降级', endpoints };
  }
  return { state: 'UNAVAILABLE', message: 'AI 模型暂不可用', endpoints };
}

/** Read the real Tax model-pool health probe; no browser-side model is assumed. */
export async function fetchAiModelStatus(signal?: AbortSignal): Promise<AiModelStatus> {
  return parseAiModelStatus(await fetchJson<unknown>('/healthz', { signal }));
}

const RAG_SYNC_TYPES: readonly RagSyncType[] = [
  'invoice',
  'contract',
  'payment',
  'tax_payment',
];

const RAG_SYNC_STATUSES = [
  'SUCCESS',
  'PENDING_REVIEW',
  'PARTIAL',
  'FAILED',
  'RUNNING',
] as const;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function positiveInteger(value: unknown): number | null {
  const number = toFiniteNumber(value, 0);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function parseRagProjectCandidate(value: unknown): RagProjectCandidate | null {
  const data = asRecord(value);
  if (!data) return null;
  const id = positiveInteger(data.id ?? data.project_id ?? data.rag_project_id);
  if (!id) return null;
  const projectCode = String(data.project_code ?? data.code ?? '').trim();
  const name = String(data.name ?? data.project_name ?? '').trim();
  const status = typeof data.status === 'string' ? data.status : undefined;
  return { id, projectCode, name, ...(status ? { status } : {}) };
}

/** Parse only projects returned by the RAG service; never synthesize candidates. */
export function extractRagProjectCandidates(payload: unknown): RagProjectCandidate[] {
  const data = asRecord(payload);
  const rawProjects = Array.isArray(payload)
    ? payload
    : data && Array.isArray(data.projects)
      ? data.projects
      : data && Array.isArray(data.items)
        ? data.items
        : [];
  const seen = new Set<number>();
  const result: RagProjectCandidate[] = [];
  for (const item of rawProjects) {
    const candidate = parseRagProjectCandidate(item);
    if (!candidate || seen.has(candidate.id)) continue;
    seen.add(candidate.id);
    result.push(candidate);
  }
  return result;
}

function parseRagStatus(payload: unknown): RagStatusResponse {
  const data = asRecord(payload);
  if (!data) throw new ApiError('RAG 状态接口返回格式不完整。', 502, payload);
  return {
    ok: data.ok === true,
    ragVersion: typeof data.rag_version === 'string' ? data.rag_version : '',
    llmExtraction: data.llm_extraction === true,
    projects: extractRagProjectCandidates(data.projects ?? payload),
    error: typeof data.error === 'string' ? data.error : '',
  };
}

function parseRagServiceSettings(payload: unknown): RagServiceSettings {
  const data = asRecord(payload);
  if (!data) throw new ApiError('RAG 设置接口返回格式不完整。', 502, payload);
  return {
    ok: data.ok === true,
    url: typeof data.url === 'string' ? data.url : '',
    host: typeof data.host === 'string' ? data.host : '',
    approvedPrivate: data.approved_private === true,
    configured: data.configured === true,
    lastTestedAt: typeof data.last_tested_at === 'string' ? data.last_tested_at : '',
    ragVersion: typeof data.rag_version === 'string' ? data.rag_version : '',
    llmExtraction: data.llm_extraction === true,
    projects: extractRagProjectCandidates(data.projects ?? payload),
    error: typeof data.error === 'string' ? data.error : '',
  };
}

/** Read the persisted RAG URL metadata; no credential is returned. */
export async function fetchRagSettings(signal?: AbortSignal): Promise<RagServiceSettings> {
  return parseRagServiceSettings(await fetchJson<unknown>('/rag-sync/settings', { signal }));
}

function ragSettingsBody(input: { url: string; approvePrivate: boolean }): Record<string, unknown> {
  const url = input.url.trim();
  if (!url) throw new ApiError('请输入 RAG 服务 IP 地址或域名。', 400);
  if (url.length > 300) throw new ApiError('RAG 服务地址不能超过 300 个字符。', 400);
  return {
    url,
    approve_private: input.approvePrivate === true,
  };
}

/** Test a new RAG URL without persisting it. */
export async function testRagSettings(input: {
  url: string;
  approvePrivate: boolean;
  signal?: AbortSignal;
}): Promise<RagServiceSettings> {
  const payload = await postJson<unknown>(
    '/rag-sync/settings/test',
    ragSettingsBody(input),
    input.signal,
  );
  return parseRagServiceSettings(payload);
}

/** Test and persist a new RAG URL using the server-side shared credential. */
export async function saveRagSettings(input: {
  url: string;
  approvePrivate: boolean;
  signal?: AbortSignal;
}): Promise<RagServiceSettings> {
  const payload = await postJson<unknown>(
    '/rag-sync/settings',
    ragSettingsBody(input),
    input.signal,
  );
  return parseRagServiceSettings(payload);
}

/** Read the server-configured RAG connection; no browser-side key is accepted. */
export async function fetchRagStatus(signal?: AbortSignal): Promise<RagStatusResponse> {
  return parseRagStatus(await fetchJson<unknown>('/rag-sync/status', { signal }));
}

function parseProjectRagMapping(payload: unknown): ProjectRagMapping {
  const data = asRecord(payload);
  if (!data) throw new ApiError('RAG 项目映射接口返回格式不完整。', 502, payload);
  const projectId = positiveInteger(data.project_id);
  const ragProjectId = positiveInteger(data.rag_project_id);
  if (!projectId || !ragProjectId) {
    throw new ApiError('RAG 项目映射缺少有效项目 ID。', 502, payload);
  }
  return {
    projectId,
    ragProjectId,
    ragProjectCode: typeof data.rag_project_code === 'string' ? data.rag_project_code : '',
    // The backend only returns this boolean. The actual credential never enters
    // the browser and is intentionally not represented in this type.
    hasApiKey: data.has_api_key === true,
    ...(typeof data.rag_url === 'string' && data.rag_url ? { ragUrl: data.rag_url } : {}),
    ...(typeof data.note === 'string' ? { note: data.note } : {}),
    ...(typeof data.synced_at === 'string' ? { syncedAt: data.synced_at } : {}),
  };
}

/** A 404 is the explicit, expected "mapping not configured" state. */
export async function fetchProjectRagMap(
  projectId: number,
  signal?: AbortSignal,
): Promise<ProjectRagMapping | null> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  try {
    const payload = await fetchJson<unknown>(`/rag-sync/project-map/${projectId}`, { signal });
    return parseProjectRagMapping(payload);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function saveProjectRagMap(input: {
  projectId: number;
  ragProjectId: number;
  ragProjectCode?: string;
  note?: string;
  signal?: AbortSignal;
}): Promise<{ projectId: number; ragProjectId: number }> {
  if (!positiveInteger(input.projectId) || !positiveInteger(input.ragProjectId)) {
    throw new ApiError('Tax 项目和 RAG 项目都必须是有效 ID。', 400);
  }
  const payload = await postJson<unknown>('/rag-sync/project-map', {
    project_id: input.projectId,
    rag_project_id: input.ragProjectId,
    rag_project_code: String(input.ragProjectCode ?? '').trim(),
    note: String(input.note ?? '').trim(),
    // Never send rag_url or rag_api_key from the browser. The server uses its
    // configured shared connection and rejects project-scoped credentials.
  }, input.signal);
  const data = asRecord(payload);
  const projectId = positiveInteger(data?.project_id);
  const ragProjectId = positiveInteger(data?.rag_project_id);
  if (data?.ok !== true || !projectId || !ragProjectId) {
    throw new ApiError('RAG 项目映射保存结果不完整。', 502, payload);
  }
  return { projectId, ragProjectId };
}

function parseRagSyncResult(
  payload: unknown,
  options: { allowBatchFailureSentinel?: boolean } = {},
): RagSyncResult {
  const data = asRecord(payload);
  if (!data) throw new ApiError('RAG 同步接口返回格式不完整。', 502, payload);
  // The backend serializes this field as an integer.  Keep the distinction
  // between a missing/malformed ID and the batch-only zero sentinel instead
  // of coercing either one into a valid-looking ID.
  const syncLogId = typeof data.sync_log_id === 'number' && Number.isInteger(data.sync_log_id)
    ? data.sync_log_id
    : null;
  const syncType = typeof data.sync_type === 'string' && RAG_SYNC_TYPES.includes(data.sync_type as RagSyncType)
    ? data.sync_type as RagSyncType
    : null;
  const status = typeof data.status === 'string' && RAG_SYNC_STATUSES.includes(
    data.status as (typeof RAG_SYNC_STATUSES)[number],
  )
    ? data.status
    : null;
  const allowBatchFailureSentinel = options.allowBatchFailureSentinel === true;
  const isBatchFailureSentinel = allowBatchFailureSentinel && status === 'FAILED' && syncLogId === 0;
  const errorsPayload = data.errors;
  const errors = Array.isArray(errorsPayload)
    ? errorsPayload.filter((item): item is string => typeof item === 'string')
    : [];
  const errorsMalformed = Array.isArray(errorsPayload)
    && errorsPayload.some(item => typeof item !== 'string');
  const failedErrorsComplete = status !== 'FAILED'
    || (Array.isArray(errorsPayload)
      && !errorsMalformed
      && errors.length > 0
      && errors.some(error => error.trim().length > 0));
  if (!syncType || !status || syncLogId === null || syncLogId < 0
    || (syncLogId === 0 && !isBatchFailureSentinel) || !failedErrorsComplete) {
    throw new ApiError('RAG 同步结果缺少有效批次信息。', 502, payload);
  }
  const numberArray = (value: unknown): number[] => Array.isArray(value)
    ? value.map(item => positiveInteger(item)).filter((item): item is number => item !== null)
    : [];
  return {
    syncLogId,
    syncType,
    status,
    totalExtracted: toFiniteNumber(data.total_extracted),
    totalImported: toFiniteNumber(data.total_imported),
    totalPending: toFiniteNumber(data.total_pending),
    importedIds: numberArray(data.imported_ids),
    pendingIds: numberArray(data.pending_ids),
    errors,
  };
}

function assertSyncType(syncType: string): asserts syncType is RagSyncType {
  if (!RAG_SYNC_TYPES.includes(syncType as RagSyncType)) {
    throw new ApiError(`不支持的 RAG 同步类型：${syncType}`, 400);
  }
}

export async function syncRagType(input: {
  projectId: number;
  ragProjectId: number;
  syncType: RagSyncType;
  signal?: AbortSignal;
}): Promise<RagSyncResult> {
  if (!positiveInteger(input.projectId) || !positiveInteger(input.ragProjectId)) {
    throw new ApiError('Tax 项目和 RAG 项目都必须是有效 ID。', 400);
  }
  assertSyncType(input.syncType);
  const payload = await postJson<unknown>('/rag-sync/sync', {
    project_id: input.projectId,
    rag_project_id: input.ragProjectId,
    extract_type: input.syncType,
  }, input.signal);
  return parseRagSyncResult(payload);
}

export async function syncRagBatch(input: {
  projectId: number;
  ragProjectId: number;
  syncTypes: RagSyncType[];
  signal?: AbortSignal;
}): Promise<RagSyncBatchResponse> {
  if (!positiveInteger(input.projectId) || !positiveInteger(input.ragProjectId)) {
    throw new ApiError('Tax 项目和 RAG 项目都必须是有效 ID。', 400);
  }
  const syncTypes = [...new Set(input.syncTypes)];
  if (syncTypes.length < 2) throw new ApiError('批量同步至少需要选择两类凭证。', 400);
  syncTypes.forEach(assertSyncType);
  const payload = await postJson<unknown>('/rag-sync/sync-batch', {
    project_id: input.projectId,
    rag_project_id: input.ragProjectId,
    extract_types: syncTypes,
  }, input.signal);
  const data = asRecord(payload);
  const projectId = positiveInteger(data?.project_id);
  if (!projectId || !Array.isArray(data?.results)) {
    throw new ApiError('RAG 批量同步结果格式不完整。', 502, payload);
  }
  return {
    projectId,
    results: data.results.map(result => parseRagSyncResult(result, { allowBatchFailureSentinel: true })),
  };
}

function stringField(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function parseRagSyncPendingContract(value: unknown): RagSyncPendingContract | null {
  const data = asRecord(value);
  const review = asRecord(data?.review);
  const id = positiveInteger(data?.id);
  const projectId = positiveInteger(data?.project_id);
  const sourceChunkId = positiveInteger(data?.source_chunk_id);
  const confidence = toFiniteNumber(data?.confidence, Number.NaN);
  if (!data || data.sync_type !== 'contract' || !id || !projectId || !sourceChunkId
    || !Number.isFinite(confidence) || confidence < 0 || confidence > 1 || !review) return null;
  return {
    id,
    projectId,
    sourceChunkId,
    filename: stringField(data.filename) || `合同待复核 #${id}`,
    pageStart: positiveInteger(data.page_start),
    confidence,
    reason: stringField(review.reason) || '后端要求人工复核。',
    partyA: { name: stringField(review.party_a_name), taxId: stringField(review.party_a_tax_id) },
    partyB: { name: stringField(review.party_b_name), taxId: stringField(review.party_b_tax_id) },
  };
}

export async function fetchRagPendingContracts(projectId: number, signal?: AbortSignal): Promise<RagSyncPendingContract[]> {
  if (!positiveInteger(projectId)) throw new ApiError('Tax 项目必须是有效 ID。', 400);
  const payload = await fetchJson<unknown>(`/rag-sync/pending?project_id=${projectId}&sync_type=contract&status=pending`, { signal });
  const data = asRecord(payload);
  if (!data || !Array.isArray(data.items)) throw new ApiError('待复核合同接口返回格式不完整。', 502, payload);
  const parsed = data.items.map(parseRagSyncPendingContract);
  if (parsed.some(item => item === null)) throw new ApiError('待复核合同包含无效记录，已停止确认操作。', 502, payload);
  return parsed as RagSyncPendingContract[];
}

export async function confirmRagPendingContractAndCreateParties(pendingId: number): Promise<RagConfirmPendingContractResult> {
  if (!positiveInteger(pendingId)) throw new ApiError('待复核记录必须是有效 ID。', 400);
  const payload = await postJson<unknown>(`/rag-sync/pending/${pendingId}/confirm-contract-and-create-parties`, { confirm: true });
  const data = asRecord(payload);
  const resultPendingId = positiveInteger(data?.pending_id);
  const recordId = positiveInteger(data?.record_id);
  const partiesRaw = Array.isArray(data?.created_external_parties) ? data.created_external_parties : null;
  if (data?.ok !== true || resultPendingId !== pendingId || !recordId || !partiesRaw) {
    throw new ApiError('合同确认接口返回格式不完整。', 502, payload);
  }
  const parties = partiesRaw.map(item => {
    const party = asRecord(item);
    const id = positiveInteger(party?.id);
    const code = stringField(party?.code);
    const name = stringField(party?.name);
    const taxId = party?.tax_id === null ? null : stringField(party?.tax_id);
    return id && code && name && (taxId === null || taxId) ? { id, code, name, taxId } : null;
  });
  if (parties.some(item => item === null)) throw new ApiError('合同确认接口返回了无效交易方。', 502, payload);
  return { pendingId: resultPendingId, recordId, createdExternalParties: parties as RagConfirmedExternalParty[] };
}

export async function rejectRagPendingContract(pendingId: number, note?: string): Promise<{ ok: boolean; pendingId: number }> {
  if (!positiveInteger(pendingId)) throw new ApiError('待复核记录必须是有效 ID。', 400);
  const payload = await postJson<unknown>(`/rag-sync/pending/${pendingId}/reject`, { note: note || '用户手动忽略/拒绝此待复核记录' });
  const data = asRecord(payload);
  const resultPendingId = positiveInteger(data?.pending_id);
  if (data?.ok !== true || resultPendingId !== pendingId) {
    throw new ApiError('待复核记录忽略接口返回格式不完整。', 502, payload);
  }
  return { ok: true, pendingId: resultPendingId };
}

export function toFiniteNumber(value: unknown, fallback = 0): number {
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function getViteEnv(): Record<string, unknown> {
  const moduleMeta = import.meta as ImportMeta & { env?: Record<string, unknown> };
  return moduleMeta.env ?? {};
}

export function configuredProjectIds(env: Record<string, unknown> = getViteEnv()): number[] {
  const raw = typeof env.VITE_PROJECT_IDS === 'string' ? env.VITE_PROJECT_IDS : '';
  return [...new Set(raw
    .split(',')
    .map(value => Number(value.trim()))
    .filter(value => Number.isInteger(value) && value > 0))];
}

/**
 * Accept only IDs returned by a real project collection response.
 *
 * The current Tax backend has no `/api/projects` collection endpoint, but this
 * parser keeps the frontend ready for that contract without inventing IDs or
 * accepting incomplete project objects as successful data.
 */
export function extractProjectIds(payload: unknown): number[] {
  const candidates = Array.isArray(payload)
    ? payload
    : payload && typeof payload === 'object' && Array.isArray((payload as Record<string, unknown>).projects)
      ? (payload as Record<string, unknown>).projects as unknown[]
      : [];

  const ids = candidates.map(item => {
    if (typeof item === 'number' || typeof item === 'string') return toFiniteNumber(item, 0);
    if (!item || typeof item !== 'object') return 0;
    const data = item as Record<string, unknown>;
    const nestedProject = data.project && typeof data.project === 'object'
      ? data.project as Record<string, unknown>
      : undefined;
    return toFiniteNumber(data.id ?? data.project_id ?? nestedProject?.id, 0);
  });

  return [...new Set(ids.filter(value => Number.isInteger(value) && value > 0))];
}

const MATCHING_COMPLETENESS_STATUSES: readonly MatchingCompletenessStatus[] = [
  'AVAILABLE',
  'DEGRADED',
  'UNAVAILABLE',
];

const MATCHING_FLOW_NAMES = ['contract', 'fulfillment', 'invoice', 'paid'] as const;

function nonNegativeInteger(value: unknown, label: string, payload: unknown): number {
  const number = toFiniteNumber(value, Number.NaN);
  if (!Number.isInteger(number) || number < 0) {
    throw new ApiError(`四流完整度接口字段 ${label} 无效。`, 502, payload);
  }
  return number;
}

function parseMatchingFlowCount(value: unknown, flow: string, payload: unknown): MatchingFlowCount {
  const data = asRecord(value);
  if (!data) throw new ApiError(`四流完整度接口缺少 ${flow} 统计。`, 502, payload);
  return {
    expected: nonNegativeInteger(data.expected, `${flow}.expected`, payload),
    available: nonNegativeInteger(data.available, `${flow}.available`, payload),
    missing: nonNegativeInteger(data.missing, `${flow}.missing`, payload),
  };
}

function parseMatchingCompleteness(
  payload: unknown,
  projectId: number,
): ProjectMatchingCompleteness {
  const data = asRecord(payload);
  if (!data) throw new ApiError('四流完整度接口返回格式不完整。', 502, payload);
  const status = data.status;
  if (!MATCHING_COMPLETENESS_STATUSES.includes(status as MatchingCompletenessStatus)) {
    throw new ApiError('四流完整度接口返回了未知状态。', 502, payload);
  }
  const counts = asRecord(data.counts);
  const rawByFlow = counts ? asRecord(counts.by_flow) : null;
  if (!counts || !rawByFlow) {
    throw new ApiError('四流完整度接口缺少统计数据。', 502, payload);
  }
  const percentageValue = data.percentage;
  let percentage: number | null = null;
  if (percentageValue !== null && percentageValue !== undefined) {
    const parsed = toFiniteNumber(percentageValue, Number.NaN);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
      throw new ApiError('四流完整度接口返回了无效百分比。', 502, payload);
    }
    percentage = parsed;
  }
  const dataGaps = Array.isArray(data.data_gaps)
    ? data.data_gaps.filter((item): item is string => typeof item === 'string')
    : [];
  return {
    projectId,
    status: status as MatchingCompletenessStatus,
    percentage,
    score: null,
    counts: {
      rows: nonNegativeInteger(counts.rows, 'rows', payload),
      expectedEvidence: nonNegativeInteger(counts.expected_evidence, 'expected_evidence', payload),
      availableEvidence: nonNegativeInteger(counts.available_evidence, 'available_evidence', payload),
      missingEvidence: nonNegativeInteger(counts.missing_evidence, 'missing_evidence', payload),
      byFlow: Object.fromEntries(
        MATCHING_FLOW_NAMES.map(flow => [flow, parseMatchingFlowCount(rawByFlow[flow], flow, payload)]),
      ),
    },
    dataGaps,
    updated: typeof data.updated === 'string' ? data.updated : '',
    source: typeof data.source === 'string' ? data.source : '',
  };
}

/** Read deterministic four-flow evidence completeness for one real project. */
export async function fetchProjectMatchingCompleteness(
  projectId: number,
  signal?: AbortSignal,
): Promise<ProjectMatchingCompleteness> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const payload = await fetchJson<unknown>(`/api/projects/${projectId}/matching/completeness`, { signal });
  return parseMatchingCompleteness(payload, projectId);
}

/**
 * Aggregate the measured evidence returned for the current project set.
 * Failed or unavailable projects make an otherwise measured result DEGRADED;
 * when nothing is measured, the result remains UNAVAILABLE with no fake 0%.
 */
export function summarizeMatchingCompleteness(
  responses: ProjectMatchingCompleteness[],
  failedCount = 0,
): MatchingCompletenessSummary {
  const measured = responses.filter(
    item => item.status !== 'UNAVAILABLE' && item.percentage !== null && item.counts.expectedEvidence > 0,
  );
  const totalExpected = measured.reduce((sum, item) => sum + item.counts.expectedEvidence, 0);
  const totalAvailable = measured.reduce((sum, item) => sum + item.counts.availableEvidence, 0);
  const hasUnavailable = responses.some(item => item.status === 'UNAVAILABLE');
  const hasDegraded = responses.some(item => item.status === 'DEGRADED');
  const dataGaps = [...new Set(responses.flatMap(item => item.dataGaps))];
  if (failedCount > 0) dataGaps.push('MATCHING_COMPLETENESS_REQUEST_FAILED');
  const hasGaps = failedCount > 0 || hasUnavailable || hasDegraded;
  if (totalExpected <= 0) {
    return { status: 'UNAVAILABLE', percentage: null, dataGaps: [...new Set(dataGaps)] };
  }
  return {
    status: hasGaps ? 'DEGRADED' : 'AVAILABLE',
    percentage: Math.round((totalAvailable / totalExpected) * 10000) / 100,
    dataGaps: [...new Set(dataGaps)],
  };
}

export interface ProjectSummaryResponse {
  project?: {
    id?: number | string;
    code?: string;
    project_code?: string;
    name?: string;
    city?: string;
    location?: string;
    contract_total?: number | string;
    contract_amount?: number | string;
  };
  revenue?: number | string;
  real_cost?: number | string;
  profit?: number | string;
  margin?: number | string;
  progress?: number | string;
  vat?: number | string;
  eac?: number | string;
}

export function mapProjectSummary(summary: ProjectSummaryResponse): ProjectItem {
  const project = summary.project ?? {};
  const numericId = toFiniteNumber(project.id, 0);
  const totalBudget = toFiniteNumber(project.contract_total ?? project.contract_amount);
  const spentAmount = toFiniteNumber(summary.real_cost);
  const progress = toFiniteNumber(summary.progress);
  return {
    id: String(project.id ?? ''),
    numericId,
    projectCode: String(project.code ?? project.project_code ?? ''),
    name: String(project.name ?? ''),
    constructionStage: '—',
    healthGrade: '未知',
    totalBudget,
    spentAmount,
    remainingBudget: Math.max(totalBudget - spentAmount, 0),
    progressPercent: progress <= 1 ? progress * 100 : progress,
    taxRiskGrade: '未知',
    isOverBudget: null,
    managerName: '—',
    location: String(project.location ?? project.city ?? '—'),
    teamAvatars: [],
    taxRecords: [],
    costItems: [],
  };
}

export interface TaxLedgerCollectionResponse {
  status: 'READY' | 'DEGRADED' | 'UNAVAILABLE' | string;
  message: string;
  items: TaxLedgerRecord[];
  total: number;
  hasMore: boolean;
}

export interface TaxLedgerRebuildResponse {
  status: string;
  period: string;
  rowCount: number;
}

/** A single unit (system-internal entity or external party) referenced by a project. */
export interface ProjectCounterparty {
  partyCode: string;
  partyName: string;
  kind: 'entity' | 'external' | 'unknown';
  isInternal: boolean;
  source: 'entities' | 'external_parties' | 'unknown';
  contractCount: number;
  contractAmount: number;
  invoiceInCount: number;
  invoiceInNet: number;
  invoiceInVat: number;
  invoiceOutCount: number;
  invoiceOutNet: number;
  invoiceOutVat: number;
  cashflowInCount: number;
  cashflowInAmount: number;
  cashflowOutCount: number;
  cashflowOutAmount: number;
  realCostCount: number;
  realCostAmount: number;
  fulfillmentCount: number;
  fulfillmentAmount: number;
}

export interface ProjectCounterpartiesResponse {
  status: 'READY' | 'EMPTY' | string;
  message: string;
  items: ProjectCounterparty[];
  total: number;
}

export interface RiskCollectionResponse {
  status: 'READY' | 'DEGRADED' | 'UNAVAILABLE';
  message: string;
  items: RiskEvent[];
  total: number;
  hasMore: boolean;
}

const RISK_SEVERITIES: readonly RiskEvent['severity'][] = ['高危', '中度', '轻度'];
const RISK_STATUSES: readonly RiskEvent['status'][] = ['待处置', '处置中', '已闭环', '已上报管理层'];

function parseRiskSeverity(value: unknown): RiskEvent['severity'] {
  return RISK_SEVERITIES.includes(value as RiskEvent['severity'])
    ? value as RiskEvent['severity']
    : '轻度';
}

function parseRiskStatus(value: unknown): RiskEvent['status'] {
  return RISK_STATUSES.includes(value as RiskEvent['status'])
    ? value as RiskEvent['status']
    : '待处置';
}

function parseRiskEvent(value: unknown): RiskEvent | null {
  const data = asRecord(value);
  if (!data) return null;
  const id = String(data.id ?? '').trim();
  if (!id) return null;
  return {
    id,
    projectName: String(data.projectName ?? data.project_name ?? '').trim(),
    entityName: String(data.entityName ?? data.entity_name ?? '').trim(),
    riskType: String(data.riskType ?? data.risk_type ?? '').trim(),
    severity: parseRiskSeverity(data.severity),
    triggerTime: String(data.triggerTime ?? data.trigger_time ?? '').trim(),
    description: String(data.description ?? '').trim(),
    auditSuggestions: String(data.auditSuggestions ?? data.audit_suggestions ?? '').trim(),
    status: parseRiskStatus(data.status),
    handler: String(data.handler ?? '未分配').trim() || '未分配',
  };
}

/** Read the real Tax risk collection for one project without inventing events. */
export async function fetchRiskEvents(
  projectId: number,
  signal?: AbortSignal,
): Promise<RiskCollectionResponse> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const query = new URLSearchParams({ project_id: String(projectId), page_size: '100' });
  const payload = await fetchJson<unknown>(`/api/risks?${query.toString()}`, { signal });
  const data = asRecord(payload);
  const status = data?.status;
  if (!data || !['READY', 'DEGRADED', 'UNAVAILABLE'].includes(status as string) || !Array.isArray(data.items)) {
    throw new ApiError('Tax 风险接口返回格式不完整。', 502, payload);
  }
  return {
    status: status as RiskCollectionResponse['status'],
    message: typeof data.message === 'string' ? data.message : '',
    items: data.items.map(parseRiskEvent).filter((item): item is RiskEvent => item !== null),
    total: toFiniteNumber(data.total),
    hasMore: data.has_more === true,
  };
}

function parseTaxRiskLevel(value: unknown): TaxLedgerRecord['riskLevel'] {
  return value === '正常' || value === '预警' || value === '高危' || value === '未知'
    ? value
    : '未知';
}

function parseTaxLedgerRecord(value: unknown): TaxLedgerRecord | null {
  const data = asRecord(value);
  if (!data) return null;
  const id = String(data.id ?? '').trim();
  if (!id) return null;
  const flow = asRecord(data.fourFlowsCheck ?? data.four_flows_check);
  return {
    id,
    entityName: String(data.entityName ?? data.entity_name ?? '').trim(),
    entityCategory: String(data.entityCategory ?? data.business_role ?? data.entity_category ?? '').trim(),
    ...(typeof data.isInternal === 'boolean' ? { isInternal: data.isInternal } : {}),
    ...(typeof data.source === 'string' ? { source: data.source } : {}),
    declareAmount: toFiniteNumber(data.declareAmount ?? data.revenue),
    taxAmount: toFiniteNumber(data.taxAmount ?? data.vat_payable),
    taxCategory: String(data.taxCategory ?? data.tax_category ?? '未知'),
    filingPeriod: String(data.filingPeriod ?? data.period ?? ''),
    status: String(data.status ?? '未知'),
    riskLevel: parseTaxRiskLevel(data.riskLevel ?? data.risk_level),
    ...(typeof data.riskDescription === 'string' ? { riskDescription: data.riskDescription } : {}),
    ...(typeof data.invoiceCode === 'string' ? { invoiceCode: data.invoiceCode } : {}),
    ...(typeof data.ragSourceDoc === 'string' ? { ragSourceDoc: data.ragSourceDoc } : {}),
    ...(typeof data.vectorSimilarity === 'number' ? { vectorSimilarity: data.vectorSimilarity } : {}),
    fourFlowsCheck: {
      contractMatch: flow?.contractMatch === true || flow?.contract_match === true,
      invoiceMatch: flow?.invoiceMatch === true || flow?.invoice_match === true,
      paymentMatch: flow?.paymentMatch === true || flow?.payment_match === true,
      logisticsMatch: flow?.logisticsMatch === true || flow?.logistics_match === true,
    },
    updateTime: String(data.updateTime ?? data.update_time ?? ''),
  };
}

/** Read the deterministic Tax ledger collection without triggering a rebuild. */
export async function fetchTaxLedger(
  projectId: number,
  signal?: AbortSignal,
): Promise<TaxLedgerCollectionResponse> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const query = new URLSearchParams({ project_id: String(projectId), page_size: '100' });
  const payload = await fetchJson<unknown>(`/api/tax-ledger?${query.toString()}`, { signal });
  const data = asRecord(payload);
  if (!data || typeof data.status !== 'string' || !Array.isArray(data.items)) {
    throw new ApiError('Tax 台账接口返回格式不完整。', 502, payload);
  }
  return {
    status: data.status,
    message: typeof data.message === 'string' ? data.message : '',
    items: data.items.map(parseTaxLedgerRecord).filter((item): item is TaxLedgerRecord => item !== null),
    total: toFiniteNumber(data.total),
    hasMore: data.has_more === true,
  };
}

function isTaxLedgerPeriod(value: string): boolean {
  return /^(?:\d{4})-(?:0[1-9]|1[0-2])$/.test(value);
}

function parseTaxLedgerRebuildResponse(payload: unknown): TaxLedgerRebuildResponse {
  const data = asRecord(payload);
  const status = typeof data?.status === 'string' ? data.status.trim() : '';
  const period = typeof data?.period === 'string' ? data.period.trim() : '';
  const rowCount = data?.row_count;
  if (!status || !isTaxLedgerPeriod(period) || typeof rowCount !== 'number' || !Number.isInteger(rowCount) || rowCount < 0) {
    throw new ApiError('Tax 台账重建接口返回格式不完整。', 502, payload);
  }
  return { status, period, rowCount };
}

/** Explicitly rebuild one month through the controlled deterministic Tax endpoint. */
export async function rebuildTaxLedger(
  period: string,
  signal?: AbortSignal,
): Promise<TaxLedgerRebuildResponse> {
  const normalizedPeriod = period.trim();
  if (!isTaxLedgerPeriod(normalizedPeriod)) {
    throw new ApiError('台账所属期必须使用 YYYY-MM 格式。', 400);
  }
  const csrfToken = readCookie('tax_csrf');
  if (!csrfToken) {
    throw new ApiError('缺少 Tax CSRF token，未执行台账重建。', 0);
  }
  const payload = await postJson<unknown>(
    '/api/tax-ledger/rebuild',
    { period: normalizedPeriod },
    signal,
    { 'X-CSRF-Token': csrfToken },
  );
  const result = parseTaxLedgerRebuildResponse(payload);
  if (result.period !== normalizedPeriod) {
    throw new ApiError('Tax 台账重建接口返回的所属期与请求不一致。', 502, payload);
  }
  return result;
}

export async function fetchAuditLogs(signal?: AbortSignal): Promise<{ items: any[]; status: any; message: string }> {
  try {
    const payload = await fetchJson<any>('/api/audit', { signal });
    const rawItems = Array.isArray(payload?.items) ? payload.items : [];
    const items = rawItems.map((item: any) => ({
      id: String(item.id ?? ''),
      timestamp: String(item.timestamp ?? ''),
      operator: String(item.operator ?? ''),
      role: String(item.role ?? '系统操作员'),
      targetSubject: String(item.targetSubject ?? item.target_subject ?? ''),
      actionType: String(item.actionType ?? item.action_type ?? ''),
      details: String(item.details ?? ''),
      integrityHash: String(item.integrityHash ?? item.integrity_hash ?? ''),
    }));
    return {
      items,
      status: payload?.status === 'READY' ? 'READY' : (payload?.status === 'DEGRADED' ? 'DEGRADED' : 'READY'),
      message: String(payload?.message ?? ''),
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    return { items: [], status: 'READY', message: '' };
  }
}

function parseCounterparty(raw: unknown): ProjectCounterparty | null {
  const data = asRecord(raw);
  if (!data) return null;
  const code = String(data.party_code ?? '').trim();
  if (!code) return null;
  const kindRaw = String(data.kind ?? 'unknown');
  const kind: ProjectCounterparty['kind'] = (
    kindRaw === 'entity' || kindRaw === 'external' || kindRaw === 'unknown'
  ) ? kindRaw : 'unknown';
  const sourceRaw = String(data.source ?? 'unknown');
  const source: ProjectCounterparty['source'] = (
    sourceRaw === 'entities' || sourceRaw === 'external_parties' || sourceRaw === 'unknown'
  ) ? sourceRaw : 'unknown';
  return {
    partyCode: code,
    partyName: String(data.party_name ?? code).trim() || code,
    kind,
    isInternal: data.isInternal === true,
    source,
    contractCount: toFiniteNumber(data.contract_count),
    contractAmount: toFiniteNumber(data.contract_amount),
    invoiceInCount: toFiniteNumber(data.invoice_in_count),
    invoiceInNet: toFiniteNumber(data.invoice_in_net),
    invoiceInVat: toFiniteNumber(data.invoice_in_vat),
    invoiceOutCount: toFiniteNumber(data.invoice_out_count),
    invoiceOutNet: toFiniteNumber(data.invoice_out_net),
    invoiceOutVat: toFiniteNumber(data.invoice_out_vat),
    cashflowInCount: toFiniteNumber(data.cashflow_in_count),
    cashflowInAmount: toFiniteNumber(data.cashflow_in_amount),
    cashflowOutCount: toFiniteNumber(data.cashflow_out_count),
    cashflowOutAmount: toFiniteNumber(data.cashflow_out_amount),
    realCostCount: toFiniteNumber(data.real_cost_count),
    realCostAmount: toFiniteNumber(data.real_cost_amount),
    fulfillmentCount: toFiniteNumber(data.fulfillment_count),
    fulfillmentAmount: toFiniteNumber(data.fulfillment_amount),
  };
}

/** Read every party the project actually references from the RAG-shared data sets. */
export async function fetchProjectCounterparties(
  projectId: number,
  signal?: AbortSignal,
): Promise<ProjectCounterpartiesResponse> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const payload = await fetchJson<unknown>(`/api/projects/${projectId}/counterparties`, { signal });
  const data = asRecord(payload);
  if (!data || !Array.isArray(data.items)) {
    throw new ApiError('对手方接口返回格式不完整。', 502, payload);
  }
  return {
    status: typeof data.status === 'string' ? data.status : 'EMPTY',
    message: typeof data.message === 'string' ? data.message : '',
    items: data.items.map(parseCounterparty).filter((item): item is ProjectCounterparty => item !== null),
    total: toFiniteNumber(data.total),
  };
}

export async function fetchProjectSummary(projectId: number, signal?: AbortSignal): Promise<ProjectItem> {
  const summary = await fetchJson<ProjectSummaryResponse>(`/api/projects/${projectId}`, { signal });
  const project = mapProjectSummary(summary);
  if (!project.id || !project.name || project.numericId <= 0) {
    throw new ApiError('项目接口返回的数据不完整，未加载该项目。', 502, summary);
  }
  return project;
}

export async function discoverProjectIds(signal?: AbortSignal): Promise<number[]> {
  const payload = await fetchJson<unknown>('/api/projects', { signal });
  const ids = extractProjectIds(payload);
  if (ids.length === 0) {
    throw new ApiError('Tax 项目集合接口返回的数据不包含有效项目 ID。', 502, payload);
  }
  return ids;
}

export async function fetchConfiguredProjects(signal?: AbortSignal): Promise<ProjectItem[]> {
  const configuredIds = configuredProjectIds();
  let ids = configuredIds;
  if (ids.length === 0) {
    try {
      ids = await discoverProjectIds(signal);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        throw new ApiError(
          '未配置 VITE_PROJECT_IDS，且 Tax 后端没有项目集合 JSON 接口。',
          0,
          error.payload,
        );
      }
      throw error;
    }
  }
  if (ids.length === 0) {
    throw new ApiError('没有可加载的真实项目 ID。', 0);
  }
  const results = await Promise.allSettled(ids.map(id => fetchProjectSummary(id, signal)));
  const projects = results
    .filter((result): result is PromiseFulfilledResult<ProjectItem> => result.status === 'fulfilled')
    .map(result => result.value);
  if (projects.length === 0) {
    const firstFailure = results.find(result => result.status === 'rejected');
    if (firstFailure?.status === 'rejected' && firstFailure.reason instanceof Error) {
      throw firstFailure.reason;
    }
    throw new ApiError('已配置的项目均无法从 Tax 服务加载。', 502);
  }
  return projects;
}

export interface PlanningResponse {
  recommended?: Record<string, unknown>;
  ai_recommendation?: Record<string, unknown>;
  scenarios?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export function postJson<T>(path: string, body: unknown, signal?: AbortSignal, headers: Record<string, string> = {}): Promise<T> {
  return fetchJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
    signal,
  });
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined' || !document.cookie) return null;
  const prefix = `${encodeURIComponent(name)}=`;
  const entry = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith(prefix));
  if (!entry) return null;
  try {
    const value = decodeURIComponent(entry.slice(prefix.length)).trim();
    return value || null;
  } catch {
    return null;
  }
}

function parseEndpointMetadata(value: unknown): AiEndpointMetadata | undefined {
  const data = asRecord(value);
  if (!data) return undefined;
  const id = positiveInteger(data.id ?? data.endpoint_id ?? data.endpointId ?? data.selected_endpoint_id ?? data.effective_endpoint_id);
  const name = typeof (data.name ?? data.endpoint_name ?? data.endpointName ?? data.selected_endpoint_name ?? data.effective_endpoint_name ?? data.provider_name ?? data.providerName) === 'string'
    ? String(data.name ?? data.endpoint_name ?? data.endpointName ?? data.selected_endpoint_name ?? data.effective_endpoint_name ?? data.provider_name ?? data.providerName).trim()
    : '';
  const model = typeof (data.model ?? data.model_name ?? data.modelName ?? data.selected_model ?? data.effective_model) === 'string'
    ? String(data.model ?? data.model_name ?? data.modelName ?? data.selected_model ?? data.effective_model).trim()
    : '';
  if (!id && !name && !model) return undefined;
  return {
    ...(id ? { id } : {}),
    ...(name ? { name } : {}),
    ...(model ? { model } : {}),
  };
}

function parseAttemptSummary(value: unknown): AiAttemptSummary | null {
  const data = asRecord(value);
  if (!data) return null;
  const endpoint = parseEndpointMetadata(data.endpoint ?? data.effective_endpoint ?? data.selected_endpoint ?? data);
  const status = typeof data.status === 'string' ? data.status : undefined;
  const error = typeof data.error === 'string'
    ? data.error
    : typeof data.error_message === 'string' ? data.error_message : undefined;
  if (!endpoint && !status && !error) return null;
  return { ...(endpoint ? { endpoint } : {}), ...(status ? { status } : {}), ...(error ? { error } : {}) };
}

/**
 * Preserve backend execution metadata without selecting or inventing an AI
 * endpoint in the browser. Backend versions may call the chosen endpoint
 * selected_endpoint or effective_endpoint, and may expose attempts as an
 * array or inside an execution envelope.
 */
export function extractAiExecutionMetadata(payload: unknown): AiExecutionMetadata {
  const data = asRecord(payload);
  if (!data) return {};
  const nested = asRecord(data.metadata ?? data.execution ?? data.ai_metadata) ?? {};
  const result = asRecord(data.result ?? data.consensus) ?? {};
  const source = { ...result, ...nested, ...data };
  const selectedEndpoint = parseEndpointMetadata(
    source.selected_endpoint ?? source.selectedEndpoint ?? source.selected,
  );
  const effectiveEndpoint = parseEndpointMetadata(
    source.effective_endpoint ?? source.effectiveEndpoint ?? source.effective,
  );
  const responseEndpoint = parseEndpointMetadata(source);
  const rawAttempts = source.attempts ?? source.endpoint_attempts ?? source.endpointAttempts;
  const attempts = Array.isArray(rawAttempts)
    ? rawAttempts.map(parseAttemptSummary).filter((item): item is AiAttemptSummary => item !== null)
    : undefined;
  const status = typeof source.status === 'string' ? source.status : undefined;
  const degraded = typeof source.degraded === 'boolean'
    ? source.degraded
    : typeof source.is_degraded === 'boolean' ? source.is_degraded : undefined;
  const fallback = typeof source.fallback === 'boolean'
    ? source.fallback
    : typeof source.is_fallback === 'boolean' ? source.is_fallback : undefined;
  return {
    ...(status ? { status } : {}),
    ...(degraded !== undefined ? { degraded } : {}),
    ...(fallback !== undefined ? { fallback } : {}),
    ...(selectedEndpoint ? { selectedEndpoint } : {}),
    ...(effectiveEndpoint ? { effectiveEndpoint } : {}),
    ...(!effectiveEndpoint && !selectedEndpoint && responseEndpoint ? { effectiveEndpoint: responseEndpoint } : {}),
    ...(attempts ? { attempts } : {}),
  };
}

export interface AiAssistantResponse {
  answer: string;
  metadata: AiExecutionMetadata;
}

export async function runAiReview(input: {
  projectId: number;
  scope: string;
  endpointId?: number;
  instruction: string;
  signal?: AbortSignal;
}): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append('project_id', String(input.projectId));
  form.append('scope', input.scope);
  const epId = positiveInteger(input.endpointId) ? input.endpointId : 1;
  form.append('endpoint_id', String(epId));
  form.append('user_instruction', input.instruction || '');
  const response = await fetch('/ai-review/run', {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    redirect: 'follow',
    signal: input.signal,
  });
  if (!response.ok) {
    const payload = await readPayload(response);
    throw new ApiError(payloadMessage(payload, `AI 审查启动失败（HTTP ${response.status}）`), response.status, payload);
  }
  const match = new URL(response.url, window.location.origin).pathname.match(/^\/ai-review\/(\d+)$/);
  if (!match) throw new ApiError('AI 审查接口未返回有效作业编号。', 502);
  return fetchJson<Record<string, unknown>>(`/api/ai-review/${match[1]}`, { signal: input.signal });
}

export async function runHealthCheck(input: {
  projectId: number;
  profile: string;
  endpointIds?: number[];
  instruction: string;
  signal?: AbortSignal;
}): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append('project_id', String(input.projectId));
  form.append('profile', input.profile);
  const validIds = (input.endpointIds ?? []).filter(id => positiveInteger(id));
  validIds.forEach(id => form.append('endpoint_ids', String(id)));
  form.append('user_instruction', input.instruction || '');
  const response = await fetch('/health-check/run', {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    redirect: 'follow',
    signal: input.signal,
  });
  if (!response.ok) {
    const payload = await readPayload(response);
    throw new ApiError(payloadMessage(payload, `AI 体检启动失败（HTTP ${response.status}）`), response.status, payload);
  }
  const match = new URL(response.url, window.location.origin).pathname.match(/^\/health-check\/(\d+)$/);
  if (!match) throw new ApiError('AI 体检接口未返回有效批次编号。', 502);
  const batchId = match[1];

  // 轮询等待后台体检作业池完成（最多等待 90 秒）
  const maxAttempts = 60;
  for (let i = 0; i < maxAttempts; i++) {
    const data = await fetchJson<Record<string, unknown>>(`/api/health-check/${batchId}`, { signal: input.signal });
    const batch = asRecord(data?.batch);
    const batchStatus = String(batch?.status ?? '').toLowerCase();
    if (batchStatus !== 'pending' && batchStatus !== 'running') {
      return data;
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
  return fetchJson<Record<string, unknown>>(`/api/health-check/${batchId}`, { signal: input.signal });
}

export async function askProjectAi(projectId: number, question: string, endpointId?: number, signal?: AbortSignal): Promise<AiAssistantResponse> {
  const form = new URLSearchParams({ question });
  if (positiveInteger(endpointId)) form.set('endpoint_id', String(endpointId));
  const response = await fetch(`/manager/project/${projectId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
    credentials: 'same-origin',
    signal,
  });
  const payload = await readPayload(response);
  if (!response.ok) throw new ApiError(payloadMessage(payload, `AI 问答失败（HTTP ${response.status}）`), response.status, payload);
  if (!payload || typeof payload !== 'object' || typeof (payload as Record<string, unknown>).answer !== 'string') {
    throw new ApiError('AI 问答接口返回格式不完整。', 502, payload);
  }
  return {
    answer: (payload as Record<string, unknown>).answer as string,
    metadata: extractAiExecutionMetadata(payload),
  };
}

export async function deleteProjectData(
  projectId: number,
  password: string,
  signal?: AbortSignal,
): Promise<{ success: boolean; project_id: number; project_name: string; message: string; deleted_counts?: Record<string, number> }> {
  return fetchJson<{ success: boolean; project_id: number; project_name: string; message: string; deleted_counts?: Record<string, number> }>(
    `/api/projects/${projectId}/delete-data`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
      signal,
    },
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
