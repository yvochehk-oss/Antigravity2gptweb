import { ApiError, fetchJson } from './api';

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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
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
  if (!/^\d{4}-(?:0[1-9]|1[0-2])$/.test(period) || !Number.isInteger(factCount) || factCount <= 0) {
    throw new ApiError('法人凭证期间接口返回了无效期间记录。', 502, payload);
  }
  return { period, factCount, isPrimary };
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
