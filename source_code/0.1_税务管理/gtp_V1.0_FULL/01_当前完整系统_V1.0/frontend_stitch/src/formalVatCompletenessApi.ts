import { ApiError, fetchJson, postJson } from './api';

export interface FormalVatCompletenessAssertion {
  exists: boolean;
  reviewed: boolean;
  assertedTotal: string | null;
  matchesObserved: boolean;
  reviewedBy: string | null;
  reviewedAt: string | null;
}

export interface FormalVatCompletenessReview {
  status: 'READY';
  entityCode: string;
  reportingPartyId: number;
  period: string;
  canReview: boolean;
  reviewRequired: boolean;
  observed: {
    outputVatTotal: string;
    inputVatTotal: string;
    outputNeedsReviewCount: number;
    inputNeedsReviewCount: number;
  };
  assertions: {
    output: FormalVatCompletenessAssertion;
    input: FormalVatCompletenessAssertion;
  };
}

const PERIOD_RE = /^\d{4}-(?:0[1-9]|1[0-2])$/;
const MONEY_RE = /^-?\d+\.\d{2}$/;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function requiredString(value: unknown, field: string, payload: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) throw new ApiError(`Formal VAT 完整性复核缺少 ${field}。`, 502, payload);
  return text;
}

function moneyString(value: unknown, field: string, payload: unknown): string {
  const text = requiredString(value, field, payload);
  if (!MONEY_RE.test(text) || !Number.isFinite(Number(text))) {
    throw new ApiError(`Formal VAT 完整性复核 ${field} 非法。`, 502, payload);
  }
  return text;
}

function nonNegativeInteger(value: unknown, field: string, payload: unknown): number {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 0) {
    throw new ApiError(`Formal VAT 完整性复核 ${field} 非法。`, 502, payload);
  }
  return number;
}

function parseAssertion(value: unknown, payload: unknown): FormalVatCompletenessAssertion {
  const row = asRecord(value);
  if (!row) throw new ApiError('Formal VAT 完整性断言状态缺失。', 502, payload);
  const assertedTotal = row.asserted_total == null
    ? null
    : moneyString(row.asserted_total, 'asserted_total', payload);
  return {
    exists: row.exists === true,
    reviewed: row.reviewed === true,
    assertedTotal,
    matchesObserved: row.matches_observed === true,
    reviewedBy: typeof row.reviewed_by === 'string' ? row.reviewed_by : null,
    reviewedAt: typeof row.reviewed_at === 'string' ? row.reviewed_at : null,
  };
}

function parseReview(payload: unknown): FormalVatCompletenessReview {
  const data = asRecord(payload);
  const observed = asRecord(data?.observed);
  const assertions = asRecord(data?.assertions);
  if (!data || data.status !== 'READY' || !observed || !assertions) {
    throw new ApiError('Formal VAT 完整性复核接口返回格式不完整。', 502, payload);
  }
  const period = requiredString(data.period, 'period', payload);
  if (!PERIOD_RE.test(period)) throw new ApiError('Formal VAT 完整性复核 period 非法。', 502, payload);
  const reportingPartyId = Number(data.reporting_party_id);
  if (!Number.isInteger(reportingPartyId) || reportingPartyId <= 0) {
    throw new ApiError('Formal VAT 完整性复核 reporting_party_id 非法。', 502, payload);
  }
  return {
    status: 'READY',
    entityCode: requiredString(data.entity_code, 'entity_code', payload),
    reportingPartyId,
    period,
    canReview: data.can_review === true,
    reviewRequired: data.review_required === true,
    observed: {
      outputVatTotal: moneyString(observed.output_vat_total, 'observed.output_vat_total', payload),
      inputVatTotal: moneyString(observed.input_vat_total, 'observed.input_vat_total', payload),
      outputNeedsReviewCount: nonNegativeInteger(
        observed.output_needs_review_count,
        'observed.output_needs_review_count',
        payload,
      ),
      inputNeedsReviewCount: nonNegativeInteger(
        observed.input_needs_review_count,
        'observed.input_needs_review_count',
        payload,
      ),
    },
    assertions: {
      output: parseAssertion(assertions.output, payload),
      input: parseAssertion(assertions.input, payload),
    },
  };
}

function normalize(entityCode: string, period: string): { entityCode: string; period: string } {
  const normalizedEntity = entityCode.trim();
  const normalizedPeriod = period.trim();
  if (!normalizedEntity) throw new ApiError('缺少法人 canonical code。', 400);
  if (!PERIOD_RE.test(normalizedPeriod)) throw new ApiError('完整性复核所属期必须使用 YYYY-MM 格式。', 400);
  return { entityCode: normalizedEntity, period: normalizedPeriod };
}

export async function fetchFormalVatCompleteness(
  entityCode: string,
  period: string,
  signal?: AbortSignal,
): Promise<FormalVatCompletenessReview> {
  const normalized = normalize(entityCode, period);
  const payload = await fetchJson<unknown>(
    `/api/v3/legal-entities/${encodeURIComponent(normalized.entityCode)}/statutory-vat/completeness?period=${encodeURIComponent(normalized.period)}`,
    { signal },
  );
  return parseReview(payload);
}

export async function reviewFormalVatCompleteness(
  entityCode: string,
  period: string,
  expectedOutputVatTotal: string,
  expectedInputVatTotal: string,
  signal?: AbortSignal,
): Promise<FormalVatCompletenessReview> {
  const normalized = normalize(entityCode, period);
  if (!MONEY_RE.test(expectedOutputVatTotal) || !MONEY_RE.test(expectedInputVatTotal)) {
    throw new ApiError('完整性复核金额必须为两位小数。', 400);
  }
  const payload = await postJson<unknown>(
    `/api/v3/legal-entities/${encodeURIComponent(normalized.entityCode)}/statutory-vat/completeness/review?period=${encodeURIComponent(normalized.period)}`,
    {
      expected_output_vat_total: expectedOutputVatTotal,
      expected_input_vat_total: expectedInputVatTotal,
    },
    signal,
  );
  return parseReview(payload);
}
