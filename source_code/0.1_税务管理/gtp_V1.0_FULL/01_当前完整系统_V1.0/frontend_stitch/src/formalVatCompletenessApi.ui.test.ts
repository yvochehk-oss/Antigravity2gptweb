import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchFormalVatCompleteness,
  reviewFormalVatCompleteness,
} from './formalVatCompletenessApi';

function payload() {
  return {
    status: 'READY',
    entity_code: 'A08',
    reporting_party_id: 8,
    period: '2026-03',
    can_review: true,
    review_required: true,
    observed: {
      output_vat_total: '130.00',
      input_vat_total: '40.00',
      output_needs_review_count: 0,
      input_needs_review_count: 0,
    },
    assertions: {
      output: {
        exists: false,
        reviewed: false,
        asserted_total: null,
        matches_observed: false,
        reviewed_by: null,
        reviewed_at: null,
      },
      input: {
        exists: false,
        reviewed: false,
        asserted_total: null,
        matches_observed: false,
        reviewed_by: null,
        reviewed_at: null,
      },
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Formal VAT completeness review frontend contract', () => {
  it('previews completeness without writing reviewed assertions', async () => {
    const requests: Array<{ path: string; method: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input, init) => {
      requests.push({ path: String(input), method: String(init?.method ?? 'GET').toUpperCase() });
      return new Response(JSON.stringify(payload()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const result = await fetchFormalVatCompleteness('A08', '2026-03');

    expect(result).toMatchObject({
      entityCode: 'A08',
      period: '2026-03',
      canReview: true,
      reviewRequired: true,
      observed: { outputVatTotal: '130.00', inputVatTotal: '40.00' },
    });
    expect(requests).toEqual([{
      path: '/api/v3/legal-entities/A08/statutory-vat/completeness?period=2026-03',
      method: 'GET',
    }]);
  });

  it('sends exact preview totals only through the explicit review endpoint', async () => {
    const requests: Array<{ path: string; method: string; body: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input, init) => {
      requests.push({
        path: String(input),
        method: String(init?.method ?? 'GET').toUpperCase(),
        body: String(init?.body ?? ''),
      });
      const reviewed = payload();
      reviewed.review_required = false;
      reviewed.assertions.output = {
        exists: true,
        reviewed: true,
        asserted_total: '130.00',
        matches_observed: true,
        reviewed_by: 'operator',
        reviewed_at: '2026-09-05T04:40:00+00:00',
      };
      reviewed.assertions.input = {
        exists: true,
        reviewed: true,
        asserted_total: '40.00',
        matches_observed: true,
        reviewed_by: 'operator',
        reviewed_at: '2026-09-05T04:40:00+00:00',
      };
      return new Response(JSON.stringify(reviewed), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const result = await reviewFormalVatCompleteness('A08', '2026-03', '130.00', '40.00');

    expect(result.reviewRequired).toBe(false);
    expect(requests).toHaveLength(1);
    expect(requests[0].path).toBe('/api/v3/legal-entities/A08/statutory-vat/completeness/review?period=2026-03');
    expect(requests[0].method).toBe('POST');
    expect(JSON.parse(requests[0].body)).toEqual({
      expected_output_vat_total: '130.00',
      expected_input_vat_total: '40.00',
    });
    expect(requests[0].path).not.toContain('/statutory-vat/rebuild');
  });
});
