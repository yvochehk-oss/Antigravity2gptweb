import { ApiError, fetchJson, postJson } from './api';
import type { DataStatus, EntityTaxLedgerRecord } from './types';

export interface LegalEntityMasterData {
  partyId: number;
  canonicalCode: string;
  legalName: string;
}

export interface LegalEntityMasterDataResponse {
  status: 'READY';
  sourceOfTruth: 'parties+internal_entities';
  items: LegalEntityMasterData[];
  total: number;
}

export interface LegalEntityFactPeriod {
  period: string;
  factCount: number;
  isPrimary: boolean;
}

export interface LegalEntityFactPeriodsResponse {
  status: 'READY' | 'EMPTY';
  entityCode: string;
  sourceOfTruth: 'analytics_canonical_facts_current';
  factType: 'invoice';
  totalFactCount: number;
  unperiodizedFactCount: number;
  periods: LegalEntityFactPeriod[];
}

export interface LegalEntityStatutoryVatResponse {
  status: DataStatus;
  message: string;
  items: EntityTaxLedgerRecord[];
  period: string;
}

export interface LegalEntityStatutoryVatRebuildResponse {
  status: 'READY' | 'DEGRADED';
  period: string;
  rowCount: number;
  failedCount: number;
  message: string;
}

const PERIOD_RE = /^\d{4}-(?:0[1-9]|1[0-2])$/;
const STATUTORY_RESOURCE_TYPE = 'FORMAL_VAT_STATUTORY_V1';
const STATUTORY_SOURCE = 'tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers';

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function requiredString(value: unknown, field: string, payload: unknown): string {
  const result = typeof value === 'string' ? value.trim() : '';
  if (!result) throw new ApiError(`Formal VAT 法定资源缺少 ${field}。`, 502, payload);
  return result;
}

function requiredPositiveInteger(value: unknown, field: string, payload: unknown): number {
  const result = Number(value);
  if (!Number.isInteger(result) || result <= 0) {
    throw new ApiError(`Formal VAT 法定资源缺少有效 ${field}。`, 502, payload);
  }
  return result;
}

function requiredMoney(value: unknown, field: string, payload: unknown): number {
  const result = Number(value);
  if (!Number.isFinite(result)) {
    throw new ApiError(`Formal VAT 法定资源缺少有效 ${field}。`, 502, payload);
  }
  return result;
}

function apiErrorCode(error: ApiError): string {
  const payload = asRecord(error.payload);
  const detail = asRecord(payload?.detail);
  return typeof detail?.code === 'string' ? detail.code : '';
}

function parseLegalEntity(value: unknown, payload: unknown): LegalEntityMasterData {
  const data = asRecord(value);
  const partyId = Number(data?.party_id);
  const canonicalCode = typeof data?.canonical_code === 'string' ? data.canonical_code.trim() : '';
  const legalName = typeof data?.legal_name === 'string' ? data.legal_name.trim() : '';
  if (!Number.isInteger(partyId) || partyId <= 0 || !canonicalCode || !legalName) {
    throw new ApiError('法人 Master Data 接口返回了无效主体记录。', 502, payload);
  }
  return { partyId, canonicalCode, legalName };
}

function parseFactPeriod(value: unknown, payload: unknown): LegalEntityFactPeriod {
  const data = asRecord(value);
  const period = typeof data?.period === 'string' ? data.period.trim() : '';
  const factCount = Number(data?.fact_count);
  const isPrimary = data?.is_primary === true;
  if (!PERIOD_RE.test(period) || !Number.isInteger(factCount) || factCount <= 0) {
    throw new ApiError('法人凭证期间接口返回了无效期间记录。', 502, payload);
  }
  return { period, factCount, isPrimary };
}

function parseFormalVatStatutoryRecord(
  payload: unknown,
  expectedEntityCode: string,
  expectedPeriod: string,
  entityName?: string,
): EntityTaxLedgerRecord {
  const data = asRecord(payload);
  const calculationRun = asRecord(data?.calculation_run);
  const vatLedger = asRecord(data?.vat_ledger);
  if (!data || data.status !== 'READY' || !calculationRun || !vatLedger) {
    throw new ApiError('Formal VAT 法定资源接口返回格式不完整。', 502, payload);
  }
  if (data.resource_type !== STATUTORY_RESOURCE_TYPE || data.source_of_truth !== STATUTORY_SOURCE) {
    throw new ApiError('Formal VAT 法定资源 source_of_truth 或 resource_type 非法。', 502, payload);
  }

  const entityCode = requiredString(data.entity_code, 'entity_code', payload);
  const period = requiredString(data.period, 'period', payload);
  if (entityCode.toUpperCase() !== expectedEntityCode.toUpperCase() || period !== expectedPeriod) {
    throw new ApiError('Formal VAT 法定资源返回的法人或期间与请求不一致。', 502, payload);
  }

  const reportingPartyId = requiredPositiveInteger(data.reporting_party_id, 'reporting_party_id', payload);
  const calculationRunId = requiredPositiveInteger(calculationRun.id, 'calculation_run.id', payload);
  const ledgerId = requiredPositiveInteger(vatLedger.id, 'vat_ledger.id', payload);
  const runStatus = requiredString(calculationRun.run_status, 'calculation_run.run_status', payload);
  if (runStatus !== 'SUCCEEDED') {
    throw new ApiError('Formal VAT 法定资源的 calculation run 不是 SUCCEEDED。', 502, payload);
  }

  return {
    id: String(ledgerId),
    period,
    entityId: reportingPartyId,
    reportingPartyId,
    entityCode,
    entityName: String(entityName || entityCode),
    businessRole: '',
    legalEntity: true,

    scope: 'LEGAL_ENTITY_STATUTORY',
    isFilingBasis: true,
    sourceOfTruth: STATUTORY_SOURCE,

    openingInputCredit: requiredMoney(vatLedger.opening_input_credit, 'vat_ledger.opening_input_credit', payload),
    outputVat: requiredMoney(vatLedger.output_vat, 'vat_ledger.output_vat', payload),
    inputVat: requiredMoney(vatLedger.input_vat, 'vat_ledger.input_vat', payload),
    taxPrepayment: requiredMoney(vatLedger.tax_prepayment, 'vat_ledger.tax_prepayment', payload),
    vatPayableBeforePrepayment: requiredMoney(
      vatLedger.vat_payable_before_prepayment,
      'vat_ledger.vat_payable_before_prepayment',
      payload,
    ),
    closingInputCredit: requiredMoney(vatLedger.closing_input_credit, 'vat_ledger.closing_input_credit', payload),
    vatPayableAfterPrepayment: requiredMoney(
      vatLedger.vat_payable_after_prepayment,
      'vat_ledger.vat_payable_after_prepayment',
      payload,
    ),
    unappliedTaxPrepayment: requiredMoney(
      vatLedger.unapplied_tax_prepayment,
      'vat_ledger.unapplied_tax_prepayment',
      payload,
    ),

    calculationRunId,
    runKind: requiredString(calculationRun.run_kind, 'calculation_run.run_kind', payload),
    runStatus,
    rulesetVersion: requiredString(calculationRun.ruleset_version, 'calculation_run.ruleset_version', payload),
    periodState: requiredString(data.period_state, 'period_state', payload),
    inputSnapshotSha256: requiredString(
      calculationRun.input_snapshot_sha256,
      'calculation_run.input_snapshot_sha256',
      payload,
    ),
    resultSha256: requiredString(calculationRun.result_sha256, 'calculation_run.result_sha256', payload),

    legalEntityVatIdentityOk: true,
    lineageComponents: [],
    dataStatus: 'READY',
    dataGaps: [],
    trusted: true,
  };
}

export async function fetchLegalEntities(signal?: AbortSignal): Promise<LegalEntityMasterDataResponse> {
  const payload = await fetchJson<unknown>('/api/v3/legal-entities?active=true&legal_entity=true', { signal });
  const data = asRecord(payload);
  if (!data || data.status !== 'READY' || !Array.isArray(data.items)) {
    throw new ApiError('法人 Master Data 接口返回格式不完整。', 502, payload);
  }
  const items = data.items.map(item => parseLegalEntity(item, payload));
  const total = Number(data.total);
  if (!Number.isInteger(total) || total < 0 || total !== items.length) {
    throw new ApiError('法人 Master Data 接口 total 与主体列表不一致。', 502, payload);
  }
  if (data.source_of_truth !== 'parties+internal_entities') {
    throw new ApiError('法人 Master Data source_of_truth 非法。', 502, payload);
  }
  return {
    status: 'READY',
    sourceOfTruth: 'parties+internal_entities',
    items,
    total,
  };
}

export async function fetchLegalEntityFactPeriods(
  entityCode: string,
  signal?: AbortSignal,
): Promise<LegalEntityFactPeriodsResponse> {
  const normalized = entityCode.trim();
  if (!normalized) throw new ApiError('缺少法人 canonical code。', 400);
  const payload = await fetchJson<unknown>(
    `/api/v3/legal-entities/${encodeURIComponent(normalized)}/fact-periods`,
    { signal },
  );
  const data = asRecord(payload);
  const status = data?.status;
  if (!data || (status !== 'READY' && status !== 'EMPTY') || !Array.isArray(data.periods)) {
    throw new ApiError('法人凭证期间接口返回格式不完整。', 502, payload);
  }
  if (data.source_of_truth !== 'analytics_canonical_facts_current' || data.fact_type !== 'invoice') {
    throw new ApiError('法人凭证期间接口 source_of_truth 或 fact_type 非法。', 502, payload);
  }
  const periods = data.periods.map(item => parseFactPeriod(item, payload));
  const primaryCount = periods.filter(item => item.isPrimary).length;
  if ((periods.length > 0 && primaryCount !== 1) || (periods.length === 0 && primaryCount !== 0)) {
    throw new ApiError('法人凭证期间接口主力期间标记不唯一。', 502, payload);
  }
  const totalFactCount = Number(data.total_fact_count);
  const unperiodizedFactCount = Number(data.unperiodized_fact_count);
  if (!Number.isInteger(totalFactCount) || totalFactCount < 0 || !Number.isInteger(unperiodizedFactCount) || unperiodizedFactCount < 0) {
    throw new ApiError('法人凭证期间接口统计字段非法。', 502, payload);
  }
  return {
    status,
    entityCode: String(data.entity_code ?? normalized),
    sourceOfTruth: 'analytics_canonical_facts_current',
    factType: 'invoice',
    totalFactCount,
    unperiodizedFactCount,
    periods,
  };
}

export async function fetchLegalEntityStatutoryVat(
  signal: AbortSignal | undefined,
  entityCode: string,
  period: string,
  entityName?: string,
): Promise<LegalEntityStatutoryVatResponse> {
  const normalizedEntity = entityCode.trim();
  const normalizedPeriod = period.trim();
  if (!normalizedEntity) throw new ApiError('缺少法人 canonical code。', 400);
  if (!PERIOD_RE.test(normalizedPeriod)) throw new ApiError('法定 VAT 所属期必须使用 YYYY-MM 格式。', 400);

  try {
    const payload = await fetchJson<unknown>(
      `/api/v3/legal-entities/${encodeURIComponent(normalizedEntity)}/statutory-vat?period=${encodeURIComponent(normalizedPeriod)}`,
      { signal },
    );
    return {
      status: 'READY',
      message: '',
      items: [parseFormalVatStatutoryRecord(payload, normalizedEntity, normalizedPeriod, entityName)],
      period: normalizedPeriod,
    };
  } catch (error) {
    if (
      error instanceof ApiError
      && error.status === 404
      && apiErrorCode(error) === 'FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND'
    ) {
      return {
        status: 'READY',
        message: '该法人当前期间暂无已生成的正式 VAT 台账。',
        items: [],
        period: normalizedPeriod,
      };
    }
    throw error;
  }
}

function chooseGroupPeriod(responses: LegalEntityFactPeriodsResponse[]): string {
  const scores = new Map<string, number>();
  for (const response of responses) {
    for (const item of response.periods) {
      scores.set(item.period, (scores.get(item.period) ?? 0) + item.factCount);
    }
  }
  return [...scores.entries()]
    .sort((left, right) => right[1] - left[1] || right[0].localeCompare(left[0]))[0]?.[0] ?? '';
}

export async function fetchLegalEntityStatutoryVatCollection(
  signal?: AbortSignal,
): Promise<LegalEntityStatutoryVatResponse> {
  const master = await fetchLegalEntities(signal);
  if (master.items.length === 0) {
    return { status: 'READY', message: '当前没有有效内部法人主体。', items: [], period: '' };
  }

  const periodResults = await Promise.allSettled(
    master.items.map(item => fetchLegalEntityFactPeriods(item.canonicalCode, signal)),
  );
  const periodResponses = periodResults
    .filter((result): result is PromiseFulfilledResult<LegalEntityFactPeriodsResponse> => result.status === 'fulfilled')
    .map(result => result.value);
  if (periodResponses.length === 0) {
    const firstFailure = periodResults.find(result => result.status === 'rejected');
    if (firstFailure?.status === 'rejected') throw firstFailure.reason;
    return { status: 'READY', message: '当前法人暂无 Canonical invoice 事实期间。', items: [], period: '' };
  }

  const selectedPeriod = chooseGroupPeriod(periodResponses);
  if (!selectedPeriod) {
    return {
      status: periodResults.some(result => result.status === 'rejected') ? 'DEGRADED' : 'READY',
      message: '当前法人暂无带有效 YYYY-MM 的 Canonical invoice 事实期间。',
      items: [],
      period: '',
    };
  }

  const statutoryResults = await Promise.allSettled(
    master.items.map(item => fetchLegalEntityStatutoryVat(
      signal,
      item.canonicalCode,
      selectedPeriod,
      item.legalName,
    )),
  );
  const successful = statutoryResults
    .filter((result): result is PromiseFulfilledResult<LegalEntityStatutoryVatResponse> => result.status === 'fulfilled')
    .map(result => result.value);
  if (successful.length === 0) {
    const firstFailure = statutoryResults.find(result => result.status === 'rejected');
    if (firstFailure?.status === 'rejected') throw firstFailure.reason;
  }

  const items = successful.flatMap(result => result.items);
  const degraded = periodResults.some(result => result.status === 'rejected')
    || statutoryResults.some(result => result.status === 'rejected');
  return {
    status: degraded ? 'DEGRADED' : 'READY',
    message: degraded
      ? `${selectedPeriod} 法人法定 VAT 读取部分失败；已保留成功返回的 Canonical Statutory 资源。`
      : items.length > 0
        ? `已按 Canonical Facts 主力期间 ${selectedPeriod} 加载 ${items.length} 条法人法定 VAT 资源。`
        : `${selectedPeriod} 暂无已生成的法人法定 VAT 资源。`,
    items,
    period: selectedPeriod,
  };
}

export async function rebuildLegalEntityStatutoryVatCollection(
  period: string,
  signal?: AbortSignal,
): Promise<LegalEntityStatutoryVatRebuildResponse> {
  const normalizedPeriod = period.trim();
  if (!PERIOD_RE.test(normalizedPeriod)) {
    throw new ApiError('台账所属期必须使用 YYYY-MM 格式。', 400);
  }

  const master = await fetchLegalEntities(signal);
  if (master.items.length === 0) {
    throw new ApiError('当前没有可执行 Formal VAT 重建的有效内部法人主体。', 409);
  }

  const results = await Promise.allSettled(
    master.items.map(item => postJson<unknown>(
      `/api/v3/legal-entities/${encodeURIComponent(item.canonicalCode)}/statutory-vat/rebuild?period=${encodeURIComponent(normalizedPeriod)}`,
      {},
      signal,
    )),
  );
  const rowCount = results.filter(result => result.status === 'fulfilled').length;
  const failedCount = results.length - rowCount;
  if (rowCount === 0) {
    const firstFailure = results.find(result => result.status === 'rejected');
    if (firstFailure?.status === 'rejected') throw firstFailure.reason;
    throw new ApiError('Formal VAT 重建未产生任何成功结果。', 409);
  }

  return {
    status: failedCount > 0 ? 'DEGRADED' : 'READY',
    period: normalizedPeriod,
    rowCount,
    failedCount,
    message: failedCount > 0
      ? `${normalizedPeriod} 已完成 ${rowCount} 个法人 Formal VAT 重建，另有 ${failedCount} 个法人被合规门禁阻止或执行失败。`
      : `${normalizedPeriod} 已完成 ${rowCount} 个法人 Formal VAT 确定性重建。`,
  };
}
