import {
  DataStatus,
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
  EntityTaxLedgerRecord,
  ProjectTaxAnalysisRecord,
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

const SAFE_HTTP_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function redirectToLoginOnUnauthorized(response: Response): void {
  if (response.status !== 401) return;
  if (typeof window === 'undefined') return;
  if (window.location.pathname === '/login') return;

  window.location.assign('/login');
}

async function secureFetch(
  input: RequestInfo | URL,
  init: ApiInit = {},
): Promise<Response> {
  const method = String(init.method ?? 'GET').toUpperCase();
  let headers = init.headers;

  if (!SAFE_HTTP_METHODS.has(method)) {
    const csrfToken = readCookie('tax_csrf');
    if (csrfToken) {
      if (!headers) {
        headers = { 'X-CSRF-Token': csrfToken };
      } else if (headers instanceof Headers) {
        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', csrfToken);
      } else if (Array.isArray(headers)) {
        if (!headers.some(([k]) => k.toLowerCase() === 'x-csrf-token')) {
          headers = [...headers, ['X-CSRF-Token', csrfToken]];
        }
      } else if (typeof headers === 'object') {
        if (!('X-CSRF-Token' in headers || 'x-csrf-token' in headers)) {
          headers = { ...(headers as Record<string, string>), 'X-CSRF-Token': csrfToken };
        }
      }
    }
  }

  const finalInit: RequestInit = {
    credentials: 'same-origin',
    ...init,
  };
  if (headers !== undefined) {
    finalInit.headers = headers;
  } else {
    delete finalInit.headers;
  }

  const response = await fetch(input, finalInit);

  redirectToLoginOnUnauthorized(response);
  return response;
}

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
    response = await secureFetch(path, init);
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
    hasApiKey: data.has_api_key === true,
    ...(typeof data.rag_url === 'string' && data.rag_url ? { ragUrl: data.rag_url } : {}),
    ...(typeof data.note === 'string' ? { note: data.note } : {}),
    ...(typeof data.synced_at === 'string' ? { syncedAt: data.synced_at } : {}),
  };
}

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
  const payload = await postJson<unknown>('/rag-sync/sync-and-recompute', {
    project_id: input.projectId,
    rag_project_id: input.ragProjectId,
    extract_types: syncTypes,
  }, input.signal);
  const data = asRecord(payload);
  const projectId = positiveInteger(data?.project_id);
  const rawResults = Array.isArray(data?.sync_results) ? data.sync_results : (Array.isArray(data?.results) ? data.results : []);
  if (!projectId || !rawResults.length) {
    throw new ApiError('RAG 批量同步结果格式不完整。', 502, payload);
  }
  return {
    projectId,
    results: rawResults.map(result => parseRagSyncResult(result, { allowBatchFailureSentinel: true })),
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
  throw new ApiError(
    'Phase 2.5 已启用 Canonical Facts 单一事实源；Tax UI 不再允许手动创建外部交易方并导入合同。',
    410,
  );
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

export async function fetchProjectMatchingCompleteness(
  projectId: number,
  signal?: AbortSignal,
): Promise<ProjectMatchingCompleteness> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const payload = await fetchJson<unknown>(`/api/projects/${projectId}/matching/completeness`, { signal });
  return parseMatchingCompleteness(payload, projectId);
}

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
    costItems: [],
  };
}

export interface TaxLedgerRebuildResponse {
  status: string;
  period: string;
  rowCount: number;
}

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

function requiredFiniteNumber(value: unknown, field: string, payload: unknown): number {
  const parsed = toFiniteNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) {
    throw new ApiError(`税务接口字段 ${field} 缺失或不是有限数值。`, 502, payload);
  }
  return parsed;
}

function requiredBoolean(value: unknown, field: string, payload: unknown): boolean {
  if (value !== true && value !== false) {
    throw new ApiError(`税务接口字段 ${field} 缺失或不是布尔值。`, 502, payload);
  }
  return value;
}

function requiredString(value: unknown, field: string, payload: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new ApiError(`税务接口字段 ${field} 缺失或不是非空字符串。`, 502, payload);
  }
  return value.trim();
}

function nullablePositiveId(value: unknown, field: string, payload: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = requiredFiniteNumber(value, field, payload);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new ApiError(`税务接口字段 ${field} 必须是正整数或 null。`, 502, payload);
  }
  return parsed;
}

function parseEntityLineageComponent(value: unknown, payload: unknown): EntityTaxLedgerRecord['lineageComponents'][number] {
  const data = asRecord(value);
  if (!data) throw new ApiError('法人 VAT lineage component 格式不完整。', 502, payload);
  const componentType = requiredString(data.component_type ?? data.componentType, 'lineage.component_type', payload);
  const invoiceFactId = nullablePositiveId(
    data.invoice_fact_id
      ?? data.invoiceFactId
      ?? data.output_invoice_fact_id
      ?? data.input_invoice_fact_id,
    'lineage.invoice_fact_id',
    payload,
  );
  const sourceDocumentId = nullablePositiveId(
    data.source_document_id
      ?? data.sourceDocumentId
      ?? data.output_source_document_id
      ?? data.input_source_document_id
      ?? data.opening_source_document_id,
    'lineage.source_document_id',
    payload,
  );
  return {
    componentType,
    amount: requiredFiniteNumber(data.amount, 'lineage.amount', payload),
    outputVatEventId: nullablePositiveId(data.output_vat_event_id ?? data.outputVatEventId, 'lineage.output_vat_event_id', payload),
    inputVatClaimId: nullablePositiveId(data.input_vat_claim_id ?? data.inputVatClaimId, 'lineage.input_vat_claim_id', payload),
    taxPrepaymentFactId: nullablePositiveId(data.tax_prepayment_fact_id ?? data.taxPrepaymentFactId, 'lineage.tax_prepayment_fact_id', payload),
    priorLedgerId: nullablePositiveId(data.prior_ledger_id ?? data.priorLedgerId, 'lineage.prior_ledger_id', payload),
    openingBalanceSeedId: nullablePositiveId(data.opening_balance_seed_id ?? data.openingBalanceSeedId, 'lineage.opening_balance_seed_id', payload),
    invoiceFactId,
    sourceDocumentId,
  };
}

function parseEntityTaxLedgerRecord(payload: unknown): EntityTaxLedgerRecord | null {
  const data = asRecord(payload);
  if (!data) return null;
  const entityCode = String(data.entity_code ?? data.entityCode ?? '').trim();
  const period = String(data.period ?? '').trim();
  if (!entityCode || !period) return null;

  const scope = requiredString(data.scope, 'scope', payload);
  if (scope !== 'LEGAL_ENTITY_STATUTORY') {
    throw new ApiError(`法人 VAT scope 非法：${scope}`, 502, payload);
  }
  const isFilingBasis = requiredBoolean(data.is_filing_basis ?? data.isFilingBasis, 'is_filing_basis', payload);
  if (!isFilingBasis) {
    throw new ApiError('法人 VAT 正式台账必须 is_filing_basis=true。', 502, payload);
  }
  const sourceOfTruth = requiredString(data.source_of_truth ?? data.sourceOfTruth, 'source_of_truth', payload);
  if (sourceOfTruth !== 'entity_vat_ledgers') {
    throw new ApiError(`法人 VAT source_of_truth 非法：${sourceOfTruth}`, 502, payload);
  }

  const entityId = nullablePositiveId(data.entity_id ?? data.entityId, 'entity_id', payload);
  const reportingPartyId = nullablePositiveId(data.reporting_party_id ?? data.reportingPartyId, 'reporting_party_id', payload);
  const calculationRunId = nullablePositiveId(data.calculation_run_id ?? data.calculationRunId, 'calculation_run_id', payload);
  if (!entityId || !reportingPartyId || !calculationRunId) {
    throw new ApiError('法人 VAT 台账缺少 entity/reporting-party/calculation-run 标识。', 502, payload);
  }
  if (entityId !== reportingPartyId) {
    throw new ApiError('法人 VAT entity_id 与 reporting_party_id 不一致。', 502, payload);
  }

  const openingInputCredit = requiredFiniteNumber(data.opening_input_credit ?? data.openingInputCredit, 'opening_input_credit', payload);
  const outputVat = requiredFiniteNumber(data.output_vat ?? data.outputVat, 'output_vat', payload);
  const inputVat = requiredFiniteNumber(data.input_vat ?? data.inputVat, 'input_vat', payload);
  const taxPrepayment = requiredFiniteNumber(data.tax_prepayment ?? data.taxPrepayment, 'tax_prepayment', payload);
  const vatPayableBeforePrepayment = requiredFiniteNumber(
    data.vat_payable_before_prepayment ?? data.vatPayableBeforePrepayment,
    'vat_payable_before_prepayment',
    payload,
  );
  const closingInputCredit = requiredFiniteNumber(data.closing_input_credit ?? data.closingInputCredit, 'closing_input_credit', payload);
  const vatPayableAfterPrepayment = requiredFiniteNumber(
    data.vat_payable_after_prepayment ?? data.vatPayableAfterPrepayment,
    'vat_payable_after_prepayment',
    payload,
  );
  const unappliedTaxPrepayment = requiredFiniteNumber(
    data.unapplied_tax_prepayment ?? data.unappliedTaxPrepayment,
    'unapplied_tax_prepayment',
    payload,
  );

  const runKind = requiredString(data.run_kind ?? data.runKind, 'run_kind', payload);
  const runStatus = requiredString(data.run_status ?? data.runStatus, 'run_status', payload);
  const rulesetVersion = requiredString(data.ruleset_version ?? data.rulesetVersion, 'ruleset_version', payload);
  const periodState = requiredString(data.period_state ?? data.periodState, 'period_state', payload);
  const inputSnapshotSha256 = requiredString(data.input_snapshot_sha256 ?? data.inputSnapshotSha256, 'input_snapshot_sha256', payload);
  const resultSha256 = requiredString(data.result_sha256 ?? data.resultSha256, 'result_sha256', payload);
  const legalEntityVatIdentityOk = requiredBoolean(
    data.legal_entity_vat_identity_ok ?? data.legalEntityVatIdentityOk,
    'legal_entity_vat_identity_ok',
    payload,
  );
  const lineageRaw = data.lineage_components ?? data.lineageComponents;
  if (!Array.isArray(lineageRaw)) {
    throw new ApiError('法人 VAT 台账缺少 lineage_components。', 502, payload);
  }

  return {
    id: String(data.id ?? `${entityCode}-${period}`),
    period,
    entityId,
    reportingPartyId,
    entityCode,
    entityName: String(data.entity_name ?? data.entityName ?? entityCode),
    businessRole: String(data.business_role ?? data.businessRole ?? ''),
    legalEntity: requiredBoolean(data.legal_entity ?? data.legalEntity, 'legal_entity', payload),

    scope: 'LEGAL_ENTITY_STATUTORY',
    isFilingBasis: true,
    sourceOfTruth,

    openingInputCredit,
    outputVat,
    inputVat,
    taxPrepayment,
    vatPayableBeforePrepayment,
    closingInputCredit,
    vatPayableAfterPrepayment,
    unappliedTaxPrepayment,

    calculationRunId,
    runKind,
    runStatus,
    rulesetVersion,
    periodState,
    inputSnapshotSha256,
    resultSha256,

    legalEntityVatIdentityOk,
    lineageComponents: lineageRaw.map(item => parseEntityLineageComponent(item, payload)),
    dataStatus: (data.data_status ?? data.dataStatus ?? 'READY') as DataStatus,
    dataGaps: Array.isArray(data.data_gaps) ? data.data_gaps.map(String) : [],
    trusted: data.trusted === true,
  };
}

function parseProjectTaxAnalysisRecord(payload: unknown): ProjectTaxAnalysisRecord | null {
  const data = asRecord(payload);
  if (!data) return null;
  const projectId = toFiniteNumber(data.project_id ?? data.projectId);
  if (projectId <= 0) return null;

  const scope = requiredString(data.scope, 'scope', payload);
  const isFilingBasis = requiredBoolean(data.is_filing_basis ?? data.isFilingBasis, 'is_filing_basis', payload);

  return {
    projectId,
    projectCode: String(data.project_code ?? data.projectCode ?? ''),
    projectName: String(data.project_name ?? data.projectName ?? ''),
    period: String(data.period ?? ''),
    entityCode: data.entity_code ? String(data.entity_code) : (data.entity ? String(data.entity) : null),

    scope,
    isFilingBasis,

    outInvoiceNet: requiredFiniteNumber(data.out_invoice_net ?? data.outInvoiceNet, 'out_invoice_net', payload),
    outInvoiceVat: requiredFiniteNumber(data.out_invoice_vat ?? data.outInvoiceVat, 'out_invoice_vat', payload),
    inInvoiceNet: requiredFiniteNumber(data.in_invoice_net ?? data.inInvoiceNet, 'in_invoice_net', payload),
    inInvoiceVat: requiredFiniteNumber(data.in_invoice_vat ?? data.inInvoiceVat, 'in_invoice_vat', payload),
    deductibleInputVat: requiredFiniteNumber(data.deductible_input_vat ?? data.deductibleInputVat, 'deductible_input_vat', payload),
    nondeductibleInputVat: requiredFiniteNumber(data.nondeductible_input_vat ?? data.nondeductibleInputVat, 'nondeductible_input_vat', payload),
    pendingInputVat: requiredFiniteNumber(data.pending_input_vat ?? data.pendingInputVat, 'pending_input_vat', payload),
    signedVatPosition: requiredFiniteNumber(data.signed_vat_position ?? data.signedVatPosition, 'signed_vat_position', payload),
    internalEliminatedNet: requiredFiniteNumber(data.internal_eliminated_net ?? data.internalEliminatedNet, 'internal_eliminated_net', payload),
    internalEliminatedVat: requiredFiniteNumber(data.internal_eliminated_vat ?? data.internalEliminatedVat, 'internal_eliminated_vat', payload),
    inputVatAccounted: requiredFiniteNumber(data.input_vat_accounted ?? data.inputVatAccounted, 'input_vat_accounted', payload),
    inputVatUnaccounted: requiredFiniteNumber(data.input_vat_unaccounted ?? data.inputVatUnaccounted, 'input_vat_unaccounted', payload),
    inputVatIdentityOk: requiredBoolean(data.input_vat_identity_ok ?? data.inputVatIdentityOk, 'input_vat_identity_ok', payload),
    realCost: requiredFiniteNumber(data.real_cost ?? data.realCost, 'real_cost', payload),
    invoiceCount: requiredFiniteNumber(data.invoice_count ?? data.invoiceCount, 'invoice_count', payload),

    sourceOfTruth: String(data.source_of_truth ?? data.sourceOfTruth ?? ''),
    legacyTablesUsed: data.legacy_tables_used === true || data.legacyTablesUsed === true,
    realCostBasis: String(data.real_cost_basis ?? data.realCostBasis ?? ''),
    dataGaps: Array.isArray(data.data_gaps) ? data.data_gaps.map(String) : [],
  };
}

export async function fetchEntityTaxLedger(
  signal?: AbortSignal,
): Promise<{
  status: DataStatus;
  message: string;
  items: EntityTaxLedgerRecord[];
}> {
  const allItems: EntityTaxLedgerRecord[] = [];
  let page = 1;
  let overallStatus: DataStatus = 'READY';
  let message = '';

  for (;;) {
    const query = new URLSearchParams({
      page: String(page),
      page_size: '100',
    });

    const payload = await fetchJson<unknown>(
      `/api/entity-tax-ledger?${query.toString()}`,
      { signal },
    );

    const data = asRecord(payload);

    if (
      !data ||
      typeof data.status !== 'string' ||
      !Array.isArray(data.items)
    ) {
      throw new ApiError(
        '法人税务台账接口返回格式不完整。',
        502,
        payload,
      );
    }

    if (data.status !== 'READY') {
      overallStatus = 'DEGRADED';
    }

    if (!message && typeof data.message === 'string') {
      message = data.message;
    }

    for (const raw of data.items) {
      const item = parseEntityTaxLedgerRecord(raw);
      if (item) allItems.push(item);
    }

    if (data.has_more !== true) break;

    page += 1;

    if (page > 1000) {
      throw new ApiError(
        '法人税务台账分页异常，已停止继续读取。',
        502,
        payload,
      );
    }
  }

  return {
    status: overallStatus,
    message,
    items: allItems,
  };
}

export async function fetchProjectTaxAnalysis(
  projectId: number,
  signal?: AbortSignal,
  period?: string,
): Promise<{
  status: DataStatus;
  message: string;
  item: ProjectTaxAnalysisRecord | null;
}> {
  if (!positiveInteger(projectId)) {
    throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  }

  const query = new URLSearchParams({
    project_id: String(projectId),
  });

  if (period) query.set('period', period);

  const payload = await fetchJson<unknown>(
    `/api/project-tax-analysis?${query.toString()}`,
    { signal },
  );

  const data = asRecord(payload);

  if (
    !data ||
    !['READY', 'DEGRADED'].includes(String(data.status)) ||
    !Array.isArray(data.items)
  ) {
    throw new ApiError(
      '项目税务分析接口返回格式不完整。',
      502,
      payload,
    );
  }

  if (data.items.length > 1) {
    throw new ApiError(
      '项目税务分析返回了非预期的多条汇总结果。',
      502,
      payload,
    );
  }

  return {
    status: data.status as DataStatus,
    message: typeof data.message === 'string' ? data.message : '',
    item:
      data.items.length === 0
        ? null
        : parseProjectTaxAnalysisRecord(data.items[0]),
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

export async function fetchProjectCounterparties(
  projectId: number,
  signal?: AbortSignal,
): Promise<ProjectCounterpartiesResponse> {
  if (!positiveInteger(projectId)) throw new ApiError('缺少有效的 Tax 项目 ID。', 400);
  const payload = await fetchJson<unknown>(`/api/v1/canonical-ssot/projects/${projectId}/counterparties`, { signal });
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
  const response = await secureFetch('/ai-review/run', {
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
  const response = await secureFetch('/health-check/run', {
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
  const response = await secureFetch(`/manager/project/${projectId}/ask`, {
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

export interface LegalEntityProjectionContribution {
  projectId: number | null;
  projectCode: string;
  projectName: string;
  revenue: number;
  bookCostProjection: number;
  accountingProfitProjection: number;
  outputVat: number;
  inputVat: number;
  deductibleInputVat: number;
  nondeductibleInputVat: number;
  pendingInputVat: number;
  internalTradeNet: number;
  internalTradeVat: number;
  factCount: number;
  factIds: number[];
}

export interface LegalEntityOperatingCumulative {
  revenue: number;
  bookCostProjection: number;
  accountingProfitProjection: number;
  outputVat: number;
  inputVat: number;
  factCount: number;
}

export interface LegalEntityOperatingProjection {
  status: Extract<DataStatus, 'READY' | 'DEGRADED'>;
  scope: 'LEGAL_ENTITY_PROJECTION';
  isFilingBasis: false;
  entityCode: string;
  period: string;
  revenue: number;
  bookCostProjection: number;
  accountingProfitProjection: number;
  outputVat: number;
  inputVat: number;
  deductibleInputVat: number;
  nondeductibleInputVat: number;
  pendingInputVat: number;
  inputVatAccounted: number;
  inputVatUnaccounted: number;
  inputVatIdentityOk: boolean;
  internalTradeNet: number;
  internalTradeVat: number;
  projectContributions: LegalEntityProjectionContribution[];
  nonProjectContribution: LegalEntityProjectionContribution;
  factCount: number;
  factIds: number[];
  cumulative: LegalEntityOperatingCumulative;
  dataGaps: string[];
  sourceOfTruth: 'analytics_canonical_facts_current';
  officialVatLedger: 'entity_vat_ledgers';
  limitations: string[];
  calculationVersion: string;
}

function projectionRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function projectionNumber(value: unknown, field: string, payload: unknown): number {
  const number = toFiniteNumber(value, Number.NaN);
  if (!Number.isFinite(number)) {
    throw new ApiError(`法人经营 Projection 字段 ${field} 缺失或不是有限数值。`, 502, payload);
  }
  return number;
}

function projectionBoolean(value: unknown, field: string, payload: unknown): boolean {
  if (value !== true && value !== false) {
    throw new ApiError(`法人经营 Projection 字段 ${field} 缺失或不是布尔值。`, 502, payload);
  }
  return value;
}

function projectionContribution(value: unknown, payload: unknown): LegalEntityProjectionContribution {
  const data = projectionRecord(value);
  if (!data) throw new ApiError('法人经营 Projection 项目贡献格式不完整。', 502, payload);
  const rawProjectId = data.project_id;
  const projectId = rawProjectId === null || rawProjectId === undefined
    ? null
    : projectionNumber(rawProjectId, 'project_id', payload);
  const factIds = Array.isArray(data.fact_ids)
    ? data.fact_ids.map(item => projectionNumber(item, 'fact_ids', payload))
    : [];
  return {
    projectId,
    projectCode: String(data.project_code ?? ''),
    projectName: String(data.project_name ?? ''),
    revenue: projectionNumber(data.revenue, 'revenue', payload),
    bookCostProjection: projectionNumber(data.book_cost_projection, 'book_cost_projection', payload),
    accountingProfitProjection: projectionNumber(data.accounting_profit_projection, 'accounting_profit_projection', payload),
    outputVat: projectionNumber(data.output_vat, 'output_vat', payload),
    inputVat: projectionNumber(data.input_vat, 'input_vat', payload),
    deductibleInputVat: projectionNumber(data.deductible_input_vat, 'deductible_input_vat', payload),
    nondeductibleInputVat: projectionNumber(data.nondeductible_input_vat, 'nondeductible_input_vat', payload),
    pendingInputVat: projectionNumber(data.pending_input_vat, 'pending_input_vat', payload),
    internalTradeNet: projectionNumber(data.internal_trade_net, 'internal_trade_net', payload),
    internalTradeVat: projectionNumber(data.internal_trade_vat, 'internal_trade_vat', payload),
    factCount: projectionNumber(data.fact_count, 'fact_count', payload),
    factIds,
  };
}

function projectionCumulative(
  value: unknown,
  payload: unknown,
  allowTopLevelFallback = false,
): LegalEntityOperatingCumulative {
  const explicit = projectionRecord(value);
  const data = explicit ?? (allowTopLevelFallback ? projectionRecord(payload) : null);
  if (!data) throw new ApiError('法人经营 Projection 缺少累计口径。', 502, payload);
  const prefix = explicit ? 'cumulative.' : '';
  const factCount = projectionNumber(data.fact_count, `${prefix}fact_count`, payload);
  if (!Number.isInteger(factCount) || factCount < 0) {
    throw new ApiError('法人经营 Projection 累计事实数必须是非负整数。', 502, payload);
  }
  return {
    revenue: projectionNumber(data.revenue, `${prefix}revenue`, payload),
    bookCostProjection: projectionNumber(data.book_cost_projection, `${prefix}book_cost_projection`, payload),
    accountingProfitProjection: projectionNumber(data.accounting_profit_projection, `${prefix}accounting_profit_projection`, payload),
    outputVat: projectionNumber(data.output_vat, `${prefix}output_vat`, payload),
    inputVat: projectionNumber(data.input_vat, `${prefix}input_vat`, payload),
    factCount,
  };
}

function parseLegalEntityOperatingProjection(
  payload: unknown,
  options: { allowCumulativeFallback?: boolean } = {},
): LegalEntityOperatingProjection {
  const data = projectionRecord(payload);
  if (!data) throw new ApiError('法人经营 Projection 接口返回格式不完整。', 502, payload);
  const status = String(data.status ?? '');
  if (status !== 'READY' && status !== 'DEGRADED') {
    throw new ApiError(`法人经营 Projection 状态非法：${status || 'EMPTY'}`, 502, payload);
  }
  if (data.scope !== 'LEGAL_ENTITY_PROJECTION' || data.is_filing_basis !== false) {
    throw new ApiError('法人经营 Projection scope / filing-basis 契约非法。', 502, payload);
  }
  if (data.source_of_truth !== 'analytics_canonical_facts_current') {
    throw new ApiError('法人经营 Projection source_of_truth 非法。', 502, payload);
  }
  if (data.official_vat_ledger !== 'entity_vat_ledgers') {
    throw new ApiError('法人经营 Projection official_vat_ledger 非法。', 502, payload);
  }
  const projectContributions = Array.isArray(data.project_contributions)
    ? data.project_contributions.map(item => projectionContribution(item, payload))
    : null;
  if (!projectContributions) {
    throw new ApiError('法人经营 Projection 缺少 project_contributions。', 502, payload);
  }
  const nonProjectContribution = projectionContribution(data.non_project_contribution, payload);
  const factIds = Array.isArray(data.fact_ids)
    ? data.fact_ids.map(item => projectionNumber(item, 'fact_ids', payload))
    : [];
  return {
    status,
    scope: 'LEGAL_ENTITY_PROJECTION',
    isFilingBasis: false,
    entityCode: String(data.entity_code ?? '').trim().toUpperCase(),
    period: String(data.period ?? ''),
    revenue: projectionNumber(data.revenue, 'revenue', payload),
    bookCostProjection: projectionNumber(data.book_cost_projection, 'book_cost_projection', payload),
    accountingProfitProjection: projectionNumber(data.accounting_profit_projection, 'accounting_profit_projection', payload),
    outputVat: projectionNumber(data.output_vat, 'output_vat', payload),
    inputVat: projectionNumber(data.input_vat, 'input_vat', payload),
    deductibleInputVat: projectionNumber(data.deductible_input_vat, 'deductible_input_vat', payload),
    nondeductibleInputVat: projectionNumber(data.nondeductible_input_vat, 'nondeductible_input_vat', payload),
    pendingInputVat: projectionNumber(data.pending_input_vat, 'pending_input_vat', payload),
    inputVatAccounted: projectionNumber(data.input_vat_accounted, 'input_vat_accounted', payload),
    inputVatUnaccounted: projectionNumber(data.input_vat_unaccounted, 'input_vat_unaccounted', payload),
    inputVatIdentityOk: projectionBoolean(data.input_vat_identity_ok, 'input_vat_identity_ok', payload),
    internalTradeNet: projectionNumber(data.internal_trade_net, 'internal_trade_net', payload),
    internalTradeVat: projectionNumber(data.internal_trade_vat, 'internal_trade_vat', payload),
    projectContributions,
    nonProjectContribution,
    factCount: projectionNumber(data.fact_count, 'fact_count', payload),
    factIds,
    cumulative: projectionCumulative(
      data.cumulative,
      payload,
      options.allowCumulativeFallback === true,
    ),
    dataGaps: Array.isArray(data.data_gaps) ? data.data_gaps.map(String) : [],
    sourceOfTruth: 'analytics_canonical_facts_current',
    officialVatLedger: 'entity_vat_ledgers',
    limitations: Array.isArray(data.limitations) ? data.limitations.map(String) : [],
    calculationVersion: String(data.calculation_version ?? ''),
  };
}

export async function fetchLegalEntityOperatingProjection(
  entityCode: string,
  period?: string,
  signal?: AbortSignal,
): Promise<LegalEntityOperatingProjection> {
  const normalizedEntityCode = entityCode.trim().toUpperCase();
  if (!normalizedEntityCode) throw new ApiError('缺少法人主体编码。', 400);
  if (period && !/^\d{4}-(?:0[1-9]|1[0-2])$/.test(period)) {
    throw new ApiError('法人经营 Projection 期间必须使用 YYYY-MM 格式。', 400);
  }
  const query = new URLSearchParams();
  if (period) query.set('period', period);
  const queryString = query.toString();
  const suffix = queryString ? `?${queryString}` : '';
  const payload = await fetchJson<unknown>(
    `/api/legal-entities/${encodeURIComponent(normalizedEntityCode)}/operating-projection${suffix}`,
    { signal },
  );
  const projection = parseLegalEntityOperatingProjection(payload, {
    allowCumulativeFallback: !period,
  });
  if (projection.entityCode !== normalizedEntityCode) {
    throw new ApiError('法人经营 Projection 返回的主体与请求不一致。', 502, payload);
  }
  if ((period ?? '') !== projection.period) {
    throw new ApiError('法人经营 Projection 返回的期间与请求不一致。', 502, payload);
  }
  return projection;
}

// ---------------------------------------------------------------------------
// TokenHub 用户级 API Key 配置（设置向导）
// ---------------------------------------------------------------------------

export interface TokenHubEndpointSummary {
  id: number;
  name: string;
  baseUrl: string;
  chatPath: string;
  model: string;
  enabled: boolean;
  routingGroup: string;
  priority: number;
  updatedAt: string | null;
  note: string;
}

export interface TokenHubSetupStatus {
  configured: boolean;
  hasUserKey: boolean;
  envFallback: boolean;
  endpoint: TokenHubEndpointSummary | null;
}

export interface TokenHubTestResult {
  ok: boolean;
  reachable: boolean;
  statusCode: number;
  error: string | null;
  models?: string[];
  formatWarning?: string | null;
}

export interface TokenHubSaveResult {
  ok: true;
  endpoint: TokenHubEndpointSummary;
  models: string[];
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function parseTokenHubEndpoint(value: unknown): TokenHubEndpointSummary | null {
  const data = asRecord(value);
  if (!data) return null;
  const id = positiveInteger(data.id);
  if (!id) return null;
  return {
    id,
    name: asString(data.name),
    baseUrl: asString(data.base_url),
    chatPath: asString(data.chat_path),
    model: asString(data.model),
    enabled: data.enabled === true,
    routingGroup: asString(data.routing_group) || 'default',
    priority: toFiniteNumber(data.priority, 0),
    updatedAt: asString(data.updated_at) || null,
    note: asString(data.note),
  };
}

export async function fetchTokenHubSetupStatus(signal?: AbortSignal): Promise<TokenHubSetupStatus> {
  const payload = await fetchJson<unknown>('/tokenhub-setup/status', { signal });
  const data = asRecord(payload);
  if (!data) {
    throw new ApiError('TokenHub 设置状态返回格式不完整。', 502, payload);
  }
  return {
    configured: data.configured === true,
    hasUserKey: data.has_user_key === true,
    envFallback: data.env_fallback === true,
    endpoint: parseTokenHubEndpoint(data.endpoint),
  };
}

export async function testTokenHubApiKey(input: {
  apiKey: string;
  signal?: AbortSignal;
}): Promise<TokenHubTestResult> {
  const apiKey = input.apiKey.trim();
  if (!apiKey) throw new ApiError('请输入 API Key。', 400);
  if (apiKey.length > 4096) throw new ApiError('API Key 长度超出 4096 字符限制。', 400);
  const payload = await postJson<unknown>(
    '/tokenhub-setup/test',
    { api_key: apiKey },
    input.signal,
  );
  const data = asRecord(payload);
  if (!data) throw new ApiError('TokenHub 测试响应格式不完整。', 502, payload);
  return {
    ok: data.ok === true,
    reachable: data.reachable === true,
    statusCode: toFiniteNumber(data.status_code, 0),
    error: asString(data.error) || null,
    models: Array.isArray(data.models)
      ? data.models.filter((item): item is string => typeof item === 'string')
      : [],
    formatWarning: asString(data.format_warning) || null,
  };
}

export async function saveTokenHubApiKey(input: {
  apiKey: string;
  model?: string;
  signal?: AbortSignal;
}): Promise<TokenHubSaveResult> {
  const apiKey = input.apiKey.trim();
  if (!apiKey) throw new ApiError('请输入 API Key。', 400);
  if (apiKey.length > 4096) throw new ApiError('API Key 长度超出 4096 字符限制。', 400);
  const body: Record<string, unknown> = { api_key: apiKey };
  const trimmedModel = (input.model || '').trim();
  if (trimmedModel) body.model = trimmedModel;
  const payload = await postJson<unknown>('/tokenhub-setup/save', body, input.signal);
  const data = asRecord(payload);
  if (!data || data.ok !== true) {
    throw new ApiError(asString(data?.detail) || 'TokenHub 保存失败。', 502, payload);
  }
  const endpoint = parseTokenHubEndpoint(data.endpoint);
  if (!endpoint) throw new ApiError('TokenHub 保存响应缺少端点信息。', 502, payload);
  return {
    ok: true,
    endpoint,
    models: Array.isArray(data.models)
      ? data.models.filter((item): item is string => typeof item === 'string')
      : [],
  };
}

export async function revokeTokenHubApiKey(signal?: AbortSignal): Promise<{ ok: boolean; message: string }> {
  const payload = await postJson<unknown>(
    '/tokenhub-setup/revoke',
    {},
    signal,
  );
  const data = asRecord(payload);
  if (!data || data.ok !== true) {
    throw new ApiError(asString(data?.detail) || '撤销 TokenHub Key 失败。', 502, payload);
  }
  return { ok: true, message: asString(data.message) || '已撤销。' };
}
