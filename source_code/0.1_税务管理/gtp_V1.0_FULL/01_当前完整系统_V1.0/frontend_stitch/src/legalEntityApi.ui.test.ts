import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchLegalEntityStatutoryVat,
  fetchLegalEntityStatutoryVatCollection,
  rebuildLegalEntityStatutoryVatCollection,
} from './legalEntityApi';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function statutoryPayload(entityCode: string, period: string, partyId: number, runId: number, ledgerId: number) {
  return {
    status: 'READY',
    resource_type: 'FORMAL_VAT_STATUTORY_V1',
    source_of_truth: 'tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers',
    entity_code: entityCode,
    reporting_party_id: partyId,
    period,
    tax_period: `${period}-01`,
    period_state: 'OPEN',
    state_version: 1,
    closed_anchor_run_id: null,
    calculation_run: {
      id: runId,
      run_kind: 'STANDARD',
      run_status: 'SUCCEEDED',
      supersedes_run_id: null,
      ruleset_version: 'V3_FORMAL_VAT_STATUTORY_V2',
      input_snapshot_sha256: 'a'.repeat(64),
      result_sha256: 'b'.repeat(64),
      completed_at: '2026-03-31T10:00:00+00:00',
    },
    vat_ledger: {
      id: ledgerId,
      opening_input_credit: '10.00',
      output_vat: '130.00',
      input_vat: '40.00',
      tax_prepayment: '20.00',
      vat_payable_before_prepayment: '80.00',
      closing_input_credit: '0.00',
      vat_payable_after_prepayment: '60.00',
      unapplied_tax_prepayment: '0.00',
      created_at: '2026-03-31T10:00:01+00:00',
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Formal VAT frontend canonical contract', () => {
  it('reads one legal entity/month only through the Canonical Statutory endpoint', async () => {
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async input => {
      requests.push(String(input));
      return jsonResponse(statutoryPayload('A08', '2026-03', 8, 108, 208));
    }));

    const result = await fetchLegalEntityStatutoryVat(undefined, 'A08', '2026-03', '四川锐宝建设工程有限公司');

    expect(requests).toEqual(['/api/v3/legal-entities/A08/statutory-vat?period=2026-03']);
    expect(result.status).toBe('READY');
    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toMatchObject({
      entityCode: 'A08',
      entityName: '四川锐宝建设工程有限公司',
      period: '2026-03',
      reportingPartyId: 8,
      calculationRunId: 108,
      outputVat: 130,
      vatPayableAfterPrepayment: 60,
      scope: 'LEGAL_ENTITY_STATUTORY',
      isFilingBasis: true,
      trusted: true,
    });
  });

  it('treats canonical 404 not-built as a normal empty state instead of UNAVAILABLE', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({
      detail: {
        code: 'FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND',
        detail: '该法人及期间尚无正式 VAT 法定资源；读取端点不会现场重算。',
      },
    }, 404)));

    const result = await fetchLegalEntityStatutoryVat(undefined, 'A08', '2026-03');

    expect(result.status).toBe('READY');
    expect(result.items).toEqual([]);
    expect(result.message).toMatch(/暂无已生成/);
  });

  it('builds group VAT view at one shared Canonical Facts period without calling the legacy collection', async () => {
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async input => {
      const path = String(input);
      requests.push(path);
      if (path === '/api/v3/legal-entities?active=true&legal_entity=true') {
        return jsonResponse({
          status: 'READY',
          source_of_truth: 'parties+internal_entities',
          total: 2,
          items: [
            { party_id: 8, canonical_code: 'A08', legal_name: '法人 A08' },
            { party_id: 1, canonical_code: 'A01', legal_name: '法人 A01' },
          ],
        });
      }
      if (path.endsWith('/A08/fact-periods')) {
        return jsonResponse({
          status: 'READY', entity_code: 'A08', source_of_truth: 'analytics_canonical_facts_current', fact_type: 'invoice',
          total_fact_count: 12, unperiodized_fact_count: 0,
          periods: [{ period: '2026-03', fact_count: 12, is_primary: true }],
        });
      }
      if (path.endsWith('/A01/fact-periods')) {
        return jsonResponse({
          status: 'READY', entity_code: 'A01', source_of_truth: 'analytics_canonical_facts_current', fact_type: 'invoice',
          total_fact_count: 10, unperiodized_fact_count: 0,
          periods: [
            { period: '2026-03', fact_count: 6, is_primary: true },
            { period: '2025-08', fact_count: 4, is_primary: false },
          ],
        });
      }
      if (path === '/api/v3/legal-entities/A08/statutory-vat?period=2026-03') {
        return jsonResponse(statutoryPayload('A08', '2026-03', 8, 108, 208));
      }
      if (path === '/api/v3/legal-entities/A01/statutory-vat?period=2026-03') {
        return jsonResponse(statutoryPayload('A01', '2026-03', 1, 101, 201));
      }
      return jsonResponse({ detail: `unexpected ${path}` }, 500);
    }));

    const result = await fetchLegalEntityStatutoryVatCollection();

    expect(result.status).toBe('READY');
    expect(result.period).toBe('2026-03');
    expect(result.items.map(item => item.entityCode).sort()).toEqual(['A01', 'A08']);
    expect(requests.some(path => path.startsWith('/api/entity-tax-ledger'))).toBe(false);
    expect(new Set(result.items.map(item => item.period))).toEqual(new Set(['2026-03']));
  });

  it('uses only per-entity Canonical Statutory rebuild POSTs and never the retired legacy writer', async () => {
    const requests: Array<{ path: string; method: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input, init) => {
      const path = String(input);
      requests.push({ path, method: String(init?.method ?? 'GET').toUpperCase() });
      if (path === '/api/v3/legal-entities?active=true&legal_entity=true') {
        return jsonResponse({
          status: 'READY', source_of_truth: 'parties+internal_entities', total: 2,
          items: [
            { party_id: 8, canonical_code: 'A08', legal_name: '法人 A08' },
            { party_id: 1, canonical_code: 'A01', legal_name: '法人 A01' },
          ],
        });
      }
      if (path.includes('/statutory-vat/rebuild?period=2026-03')) {
        return jsonResponse({ status: 'BUILT' });
      }
      return jsonResponse({ detail: `unexpected ${path}` }, 500);
    }));

    const result = await rebuildLegalEntityStatutoryVatCollection('2026-03');

    expect(result).toMatchObject({ status: 'READY', period: '2026-03', rowCount: 2, failedCount: 0 });
    expect(requests.filter(item => item.method === 'POST').map(item => item.path).sort()).toEqual([
      '/api/v3/legal-entities/A01/statutory-vat/rebuild?period=2026-03',
      '/api/v3/legal-entities/A08/statutory-vat/rebuild?period=2026-03',
    ]);
    expect(requests.some(item => item.path === '/api/tax-ledger/rebuild')).toBe(false);
  });

  it('runtime VAT consumers no longer import or invoke the legacy entity-tax-ledger client', () => {
    const app = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');
    const entityView = readFileSync(resolve(process.cwd(), 'src/components/EntityCorporateView.tsx'), 'utf8');
    const adapter = readFileSync(resolve(process.cwd(), 'src/legalEntityApi.ts'), 'utf8');

    expect(app).toMatch(/fetchLegalEntityStatutoryVatCollection/);
    expect(app).not.toMatch(/\bfetchEntityTaxLedger\b/);
    expect(entityView).toMatch(/fetchLegalEntityStatutoryVat\(controller\.signal, entityCode, period/);
    expect(entityView).not.toMatch(/api\.fetchEntityTaxLedger/);
    expect(adapter).not.toContain('/api/entity-tax-ledger');
    expect(adapter).not.toContain('/api/tax-ledger/rebuild');
  });
});
