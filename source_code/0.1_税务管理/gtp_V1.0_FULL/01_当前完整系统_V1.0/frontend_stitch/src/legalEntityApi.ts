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
