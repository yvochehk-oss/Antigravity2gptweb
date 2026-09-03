import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const PAGES = [
  ['dashboard', 'DashboardView.tsx'],
  ['entity-profile', 'EntityCorporateView.tsx'],
  ['tax-ledger', 'TaxLedgerView.tsx'],
  ['projects', 'ProjectRepositoryView.tsx'],
  ['ai-decision', 'AiDecisionCenterView.tsx'],
  ['risk-center', 'RiskCenterView.tsx'],
  ['audit', 'AuditView.tsx'],
] as const;

function source(file: string): string {
  return readFileSync(resolve(process.cwd(), 'src/components', file), 'utf8');
}

describe('seven business pages unified layout and Chinese UI governance', () => {
  it.each(PAGES)('%s keeps its page title independent from controls', (pageId, file) => {
    const text = source(file);
    const titleMarker = `data-page-title="${pageId}"`;
    const controlsMarker = `data-page-controls="${pageId}"`;
    expect(text).toContain(titleMarker);
    expect(text).toContain(controlsMarker);
    expect(text.indexOf(titleMarker)).toBeLessThan(text.indexOf(controlsMarker));
  });

  it('does not reintroduce known English jargon in business-facing literal copy', () => {
    const text = PAGES.map(([, file]) => source(file)).join('\n');
    for (const forbidden of [
      'Statutory VAT',
      'LEGAL_ENTITY_STATUTORY ·',
      'LEGAL_ENTITY_PROJECTION ·',
      'Project Boundary',
      'Tax API 项目数量',
      'Run 状态',
      'CIT / 税后利润',
      'NOT AVAILABLE',
      'FACT-BASED',
      'SCENARIO ·',
      'SIMULATION ·',
      'DEGRADED ·',
      'UNAVAILABLE ·',
      '销项 VAT',
      '进项 VAT',
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
