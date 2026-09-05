import { ApiError } from './api';

export type ExcelExportView = 'current' | 'cumulative';

function exportPeriodOk(value: string): boolean {
  return /^\d{4}-(?:0[1-9]|1[0-2])$/.test(value);
}

function filenameFromDisposition(value: string | null): string {
  if (!value) return 'tax-export.xlsx';
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return 'tax-export.xlsx';
    }
  }
  const plain = value.match(/filename="?([^";]+)"?/i)?.[1];
  return plain?.trim() || 'tax-export.xlsx';
}

async function errorMessage(response: Response): Promise<{ message: string; payload?: unknown }> {
  const text = await response.text();
  if (!text) return { message: `Excel 导出失败（HTTP ${response.status}）` };
  try {
    const payload = JSON.parse(text) as unknown;
    if (payload && typeof payload === 'object') {
      const detail = (payload as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail.trim()) return { message: detail, payload };
    }
    return { message: `Excel 导出失败（HTTP ${response.status}）`, payload };
  } catch {
    return { message: text, payload: text };
  }
}

export async function fetchExcelExport(
  scope: string,
  view: ExcelExportView,
  period?: string,
  signal?: AbortSignal,
): Promise<{ filename: string; size: number }> {
  const normalizedScope = scope.trim().toUpperCase() || 'ALL';
  if (view !== 'current' && view !== 'cumulative') {
    throw new ApiError('Excel 导出 view 必须为 current 或 cumulative。', 400);
  }
  const normalizedPeriod = String(period ?? '').trim();
  if (normalizedPeriod && !exportPeriodOk(normalizedPeriod)) {
    throw new ApiError('Excel 导出期间必须使用 YYYY-MM 格式。', 400);
  }
  if (view === 'current' && !normalizedPeriod) {
    throw new ApiError('当期 Excel 导出必须指定所属期间。', 400);
  }

  const query = new URLSearchParams({ scope: normalizedScope, view });
  if (normalizedPeriod) query.set('period', normalizedPeriod);

  let response: Response;
  try {
    response = await fetch(`/api/v3/export/excel?${query.toString()}`, {
      method: 'GET',
      credentials: 'same-origin',
      signal,
      headers: { Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('无法连接 Tax 服务，Excel 导出未执行。');
  }

  if (response.status === 401 && typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
  if (!response.ok) {
    const parsed = await errorMessage(response);
    throw new ApiError(parsed.message, response.status, parsed.payload);
  }

  const contentType = response.headers.get('Content-Type') || '';
  if (!contentType.toLowerCase().includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
    throw new ApiError('Tax 服务返回的不是 Excel 工作簿。', 502);
  }

  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition'));
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  return { filename, size: blob.size };
}

export const __excelExportTesting = { filenameFromDisposition, exportPeriodOk };
