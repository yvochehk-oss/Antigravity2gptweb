export * from './api.base';

import { ApiError, fetchJson, toFiniteNumber } from './api.base';
import type { DataStatus } from './types';

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

function parseLegalEntityOperatingProjection(payload: unknown): LegalEntityOperatingProjection {
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
  const projection = parseLegalEntityOperatingProjection(payload);
  if (projection.entityCode !== normalizedEntityCode) {
    throw new ApiError('法人经营 Projection 返回的主体与请求不一致。', 502, payload);
  }
  if ((period ?? '') !== projection.period) {
    throw new ApiError('法人经营 Projection 返回的期间与请求不一致。', 502, payload);
  }
  return projection;
}
