import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { __excelExportTesting } from './excelExportApi';

describe('Excel export client contract', () => {
  it('accepts canonical periods and decodes RFC 5987 filenames', () => {
    expect(__excelExportTesting.exportPeriodOk('2026-03')).toBe(true);
    expect(__excelExportTesting.exportPeriodOk('2026-13')).toBe(false);
    expect(__excelExportTesting.filenameFromDisposition(
      "attachment; filename=tax-export.xlsx; filename*=UTF-8''%E7%A8%8E%E5%8A%A1.xlsx",
    )).toBe('税务.xlsx');
  });

  it('all three primary pages call the xlsx export client directly', () => {
    const dashboard = readFileSync(resolve(__dirname, './components/DashboardView.tsx'), 'utf8');
    const projects = readFileSync(resolve(__dirname, './components/ProjectRepositoryView.tsx'), 'utf8');
    const taxLedger = readFileSync(resolve(__dirname, './components/TaxLedgerView.tsx'), 'utf8');

    for (const source of [dashboard, projects, taxLedger]) {
      expect(source).toMatch(/fetchExcelExport/);
    }
    expect(dashboard).toMatch(/导出多 Sheet Excel/);
    expect(projects).toMatch(/导出工程库多 Sheet Excel/);
    expect(taxLedger).toMatch(/导出 6 Sheet Excel/);
    expect(taxLedger).not.toMatch(/triggerCsvDownload/);
    expect(taxLedger).not.toMatch(/text\/csv/);
  });

  it('download client targets the v3 Excel endpoint and validates xlsx media type', () => {
    const source = readFileSync(resolve(__dirname, './excelExportApi.ts'), 'utf8');
    expect(source).toMatch(/\/api\/v3\/export\/excel/);
    expect(source).toMatch(/application\/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet/);
    expect(source).toMatch(/credentials: 'same-origin'/);
  });
});


