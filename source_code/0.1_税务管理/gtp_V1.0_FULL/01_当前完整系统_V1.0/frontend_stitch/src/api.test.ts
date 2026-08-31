import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  ApiError,
  configuredProjectIds,
  extractProjectIds,
  extractRagProjectCandidates,
  fetchProjectCounterparties,
  fetchProjectRagMap,
  fetchProjectMatchingCompleteness,
  fetchAiModelStatus,
  fetchRagSettings,
  fetchRagStatus,
  fetchRagPendingContracts,
  confirmRagPendingContractAndCreateParties,
  fetchRiskEvents,
  fetchTaxLedger,
  rebuildTaxLedger,
  mapProjectSummary,
  saveProjectRagMap,
  saveRagSettings,
  summarizeMatchingCompleteness,
  syncRagBatch,
  syncRagType,
  testRagSettings,
  extractAiExecutionMetadata,
  parseAiModelStatus,
} from './api';
import { TAX_LEDGER_EMPTY_MESSAGE } from './components/TaxLedgerView';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function matchingCompletenessPayload(
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE',
  percentage: number | null,
): Record<string, unknown> {
  return {
    status,
    score: null,
    percentage,
    counts: {
      rows: percentage === null ? 0 : 1,
      expected_evidence: percentage === null ? 0 : 4,
      available_evidence: percentage === null ? 0 : Math.round((percentage / 100) * 4),
      missing_evidence: percentage === null ? 0 : 4 - Math.round((percentage / 100) * 4),
      by_flow: {
        contract: { expected: percentage === null ? 0 : 1, available: percentage === null ? 0 : 1, missing: 0 },
        fulfillment: { expected: percentage === null ? 0 : 1, available: percentage === null ? 0 : 1, missing: 0 },
        invoice: { expected: percentage === null ? 0 : 1, available: percentage === null ? 0 : 1, missing: 0 },
        paid: { expected: percentage === null ? 0 : 1, available: percentage === null ? 0 : 1, missing: 0 },
      },
    },
    data_gaps: percentage === null ? ['NO_MATCHING_ROWS'] : [],
    updated: '2026-08-24T00:00:00+00:00',
    source: 'tax.deterministic.matching_rows',
  };
}

async function withMockFetch(
  handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
  callback: () => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = handler as typeof fetch;
  try {
    await callback();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test('configuredProjectIds accepts only positive unique numeric IDs', () => {
  assert.deepEqual(configuredProjectIds({ VITE_PROJECT_IDS: '3, 2, 3, invalid, 0, -1' }), [3, 2]);
  assert.deepEqual(configuredProjectIds({ VITE_PROJECT_IDS: '' }), []);
});

test('extractAiExecutionMetadata preserves backend selected/effective endpoint and attempts', () => {
  assert.deepEqual(extractAiExecutionMetadata({
    status: 'DEGRADED',
    effective_endpoint: { id: 7, name: 'primary', model: 'model-a' },
    fallback: true,
    attempts: [{ endpoint_id: 8, endpoint_name: 'fallback', model_name: 'model-b', status: 'timeout', error_message: '超时' }],
  }), {
    status: 'DEGRADED',
    fallback: true,
    effectiveEndpoint: { id: 7, name: 'primary', model: 'model-a' },
    attempts: [{ endpoint: { id: 8, name: 'fallback', model: 'model-b' }, status: 'timeout', error: '超时' }],
  });
});

test('fetchAiModelStatus presents the real Tax model pool health without a static placeholder', async () => {
  const requests: string[] = [];
  await withMockFetch(async input => {
    requests.push(String(input));
    return jsonResponse({
      status: 'ok',
      components: {
        ai: {
          status: 'ok',
          endpoints: [{ name: 'Qwen 8930', status: 'ok' }],
        },
      },
    });
  }, async () => {
    const result = await fetchAiModelStatus();
    assert.equal(result.state, 'READY');
    assert.equal(result.message, 'AI 模型已就绪');
    assert.deepEqual(result.endpoints, [{ name: 'Qwen 8930', status: 'ok' }]);
  });
  assert.deepEqual(requests, ['/healthz']);
});

test('AI model health is degraded only when a real endpoint remains healthy', () => {
  assert.equal(parseAiModelStatus({ components: { ai: {
    status: 'degraded',
    endpoints: [
      { name: 'Qwen 8930', status: 'ok' },
      { name: 'fallback', status: 'down' },
    ],
  } } }).state, 'DEGRADED');
  assert.equal(parseAiModelStatus({ components: { ai: {
    status: 'degraded',
    endpoints: [{ name: 'Qwen 8930', status: 'down' }],
  } } }).state, 'UNAVAILABLE');
  assert.throws(() => parseAiModelStatus({ components: {} }), /格式不完整/);
});

test('Sidebar and App expose unavailable health request explicitly', () => {
  const sidebar = readFileSync(new URL('./components/Sidebar.tsx', import.meta.url), 'utf8');
  const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8');
  assert.equal(sidebar.includes('AI 模型状态未加载'), false);
  assert.match(sidebar, /aiModelStatus\.message/);
  assert.match(app, /状态暂不可用/);
});

test('RiskCenter renders collection status without claiming the API is absent', () => {
  const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8');
  const riskCenter = readFileSync(new URL('./components/RiskCenterView.tsx', import.meta.url), 'utf8');
  assert.match(app, /dataStatusMessage=\{riskStatusMessage\}/);
  assert.match(riskCenter, /暂无已识别风险事件/);
  assert.match(riskCenter, /dataStatus === 'DEGRADED'/);
  assert.equal(riskCenter.includes('当前 Tax 后端没有风险集合 JSON 接口'), false);
});

test('extractProjectIds accepts only real positive IDs from collection payloads', () => {
  assert.deepEqual(
    extractProjectIds([
      { id: 7 },
      { project_id: '8' },
      { project: { id: 9 } },
      7,
      { id: 0 },
      { name: '没有 ID 的项目' },
    ]),
    [7, 8, 9],
  );
  assert.deepEqual(extractProjectIds({ projects: [{ id: 11 }, { id: -1 }] }), [11]);
  assert.deepEqual(extractProjectIds({ items: [{ id: 12 }] }), []);
});

test('fetchProjectMatchingCompleteness parses AVAILABLE, DEGRADED and UNAVAILABLE states', async () => {
  const requests: string[] = [];
  await withMockFetch(async (input, _init) => {
    const path = String(input);
    requests.push(path);
    const projectId = Number(path.match(/projects\/(\d+)\//)?.[1] ?? 0);
    if (projectId === 1) return jsonResponse(matchingCompletenessPayload('AVAILABLE', 100));
    if (projectId === 2) return jsonResponse(matchingCompletenessPayload('DEGRADED', 50));
    return jsonResponse(matchingCompletenessPayload('UNAVAILABLE', null));
  }, async () => {
    const available = await fetchProjectMatchingCompleteness(1);
    const degraded = await fetchProjectMatchingCompleteness(2);
    const unavailable = await fetchProjectMatchingCompleteness(3);
    assert.equal(available.status, 'AVAILABLE');
    assert.equal(available.percentage, 100);
    assert.equal(degraded.status, 'DEGRADED');
    assert.equal(degraded.percentage, 50);
    assert.equal(unavailable.status, 'UNAVAILABLE');
    assert.equal(unavailable.percentage, null);
  });
  assert.deepEqual(requests, [
    '/api/projects/1/matching/completeness',
    '/api/projects/2/matching/completeness',
    '/api/projects/3/matching/completeness',
  ]);
});

test('matching completeness summary stays fail-closed for HTTP failures and unavailable data', () => {
  const available = {
    projectId: 1,
    status: 'AVAILABLE' as const,
    percentage: 100,
    score: null,
    counts: { rows: 1, expectedEvidence: 4, availableEvidence: 4, missingEvidence: 0, byFlow: {} },
    dataGaps: [],
    updated: '',
    source: '',
  };
  const degraded = {
    ...available,
    projectId: 2,
    status: 'DEGRADED' as const,
    percentage: 50,
    counts: { ...available.counts, availableEvidence: 2, missingEvidence: 2 },
    dataGaps: ['MISSING_INVOICE_EVIDENCE'],
  };
  const unavailable = {
    ...available,
    projectId: 3,
    status: 'UNAVAILABLE' as const,
    percentage: null,
    counts: { ...available.counts, rows: 0, expectedEvidence: 0, availableEvidence: 0, missingEvidence: 0 },
    dataGaps: ['NO_MATCHING_ROWS'],
  };
  assert.deepEqual(summarizeMatchingCompleteness([available]), {
    status: 'AVAILABLE', percentage: 100, dataGaps: [],
  });
  assert.deepEqual(summarizeMatchingCompleteness([degraded]), {
    status: 'DEGRADED', percentage: 50, dataGaps: ['MISSING_INVOICE_EVIDENCE'],
  });
  assert.deepEqual(summarizeMatchingCompleteness([unavailable]), {
    status: 'UNAVAILABLE', percentage: null, dataGaps: ['NO_MATCHING_ROWS'],
  });
  assert.equal(summarizeMatchingCompleteness([{ ...unavailable, percentage: 80 }]).status, 'UNAVAILABLE');
  assert.equal(summarizeMatchingCompleteness([{ ...unavailable, percentage: 80 }]).percentage, null);
  const failed = summarizeMatchingCompleteness([available], 1);
  assert.equal(failed.status, 'DEGRADED');
  assert.equal(failed.percentage, 100);
  assert.deepEqual(failed.dataGaps, ['MATCHING_COMPLETENESS_REQUEST_FAILED']);
});

test('matching completeness API errors are propagated and the card has no static health placeholder', async () => {
  await withMockFetch(async () => jsonResponse({ detail: '禁止访问' }, 403), async () => {
    await assert.rejects(() => fetchProjectMatchingCompleteness(1), error => {
      return error instanceof Error && 'status' in error && (error as { status: number }).status === 403;
    });
  });
  const component = readFileSync(new URL('./components/ProjectRepositoryView.tsx', import.meta.url), 'utf8');
  assert.equal(component.includes('履约健康度'), false);
  assert.equal(component.includes('接口未提供'), false);
  assert.match(component, /四流证据完整度/);
});

test('mapProjectSummary keeps only values returned by the project API', () => {
  const project = mapProjectSummary({
    project: {
      id: 7,
      code: 'P-7',
      name: '真实项目',
      city: '成都',
      contract_total: '1000.00',
    },
    revenue: '400.00',
    real_cost: '250.00',
    progress: '0.4',
  });
  assert.equal(project.numericId, 7);
  assert.equal(project.totalBudget, 1000);
  assert.equal(project.spentAmount, 250);
  assert.equal(project.progressPercent, 40);
  assert.equal(project.taxRiskGrade, '未知');
  assert.equal(project.taxRecords.length, 0);
  assert.equal(project.managerName, '—');
});

test('extractRagProjectCandidates accepts only backend project IDs and deduplicates', () => {
  assert.deepEqual(
    extractRagProjectCandidates({ projects: [
      { id: 4, project_code: 'R-4', name: 'RAG 项目 4' },
      { project_id: '4', name: '重复项目' },
      { id: 0, project_code: 'invalid' },
      { name: '没有 ID' },
    ] }),
    [{ id: 4, projectCode: 'R-4', name: 'RAG 项目 4' }],
  );
});

test('fetchRagStatus reads the server status and never sends a browser API key', async () => {
  await withMockFetch(async (input, init) => {
    assert.equal(String(input), '/rag-sync/status');
    assert.equal(init?.credentials, 'same-origin');
    assert.equal(init?.headers, undefined);
    return jsonResponse({
      ok: true,
      rag_version: '1.1.0',
      llm_extraction: true,
      projects: [{ id: 8, project_code: 'R-8', name: '真实 RAG 项目' }],
    });
  }, async () => {
    const result = await fetchRagStatus();
    assert.equal(result.ok, true);
    assert.equal(result.ragVersion, '1.1.0');
    assert.deepEqual(result.projects[0], { id: 8, projectCode: 'R-8', name: '真实 RAG 项目' });
  });
});

test('RAG settings API persists only URL approval metadata and never sends a credential', async () => {
  const requests: Array<{ path: string; body?: Record<string, unknown> }> = [];
  await withMockFetch(async (input, init) => {
    const path = String(input);
    const body = init?.body === undefined
      ? undefined
      : JSON.parse(String(init.body)) as Record<string, unknown>;
    requests.push({ path, body });
    if (path === '/rag-sync/settings') {
      return jsonResponse({
        ok: true,
        url: 'https://rag.example.test:8922',
        host: 'rag.example.test',
        approved_private: false,
        configured: true,
        last_tested_at: '2026-08-25T09:00:00+00:00',
        rag_version: '1.1.0',
        llm_extraction: true,
        projects: [{ id: 18, project_code: 'R-18', name: 'RAG 项目' }],
      });
    }
    if (path === '/rag-sync/settings/test') {
      return jsonResponse({
        ok: true,
        url: 'http://192.168.1.20:8922',
        host: '192.168.1.20',
        approved_private: true,
        configured: false,
        rag_version: '1.1.0',
        projects: [],
      });
    }
    return jsonResponse({
      ok: false,
      error: 'unexpected path',
    }, 500);
  }, async () => {
    const current = await fetchRagSettings();
    assert.equal(current.configured, true);
    assert.equal(current.host, 'rag.example.test');
    assert.equal(current.projects[0].id, 18);

    const tested = await testRagSettings({
      url: 'http://192.168.1.20:8922',
      approvePrivate: true,
    });
    assert.equal(tested.approvedPrivate, true);

    const saved = await saveRagSettings({
      url: 'https://rag.example.test:8922',
      approvePrivate: false,
    });
    assert.equal(saved.configured, true);
  });

  assert.deepEqual(requests[0], { path: '/rag-sync/settings', body: undefined });
  assert.deepEqual(requests[1], {
    path: '/rag-sync/settings/test',
    body: { url: 'http://192.168.1.20:8922', approve_private: true },
  });
  assert.deepEqual(requests[2], {
    path: '/rag-sync/settings',
    body: { url: 'https://rag.example.test:8922', approve_private: false },
  });
  for (const request of requests) {
    assert.equal(request.body ? 'api_key' in request.body : false, false);
    assert.equal(request.body ? 'rag_api_key' in request.body : false, false);
  }
});

test('RAG settings client rejects a blank or overlong URL before making a request', async () => {
  await assert.rejects(
    () => testRagSettings({ url: '   ', approvePrivate: false }),
    error => error instanceof Error && error.message.includes('IP 地址或域名'),
  );
  await assert.rejects(
    () => saveRagSettings({ url: 'x'.repeat(301), approvePrivate: false }),
    error => error instanceof Error && error.message.includes('300 个字符'),
  );
});

test('fetchProjectRagMap treats only backend 404 as an unconfigured mapping', async () => {
  await withMockFetch(async (_input, _init) => jsonResponse({ detail: '项目未配置 RAG 映射' }, 404), async () => {
    assert.equal(await fetchProjectRagMap(6), null);
  });
  await withMockFetch(async (_input, _init) => jsonResponse({ detail: '禁止' }, 403), async () => {
    await assert.rejects(() => fetchProjectRagMap(6), error => {
      return error instanceof Error && 'status' in error && (error as { status: number }).status === 403;
    });
  });
});

test('save and sync APIs use explicit project IDs and no project-scoped secret', async () => {
  const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await withMockFetch(async (input, init) => {
    const path = String(input);
    const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    requests.push({ path, body });
    if (path === '/rag-sync/project-map') return jsonResponse({ ok: true, project_id: 6, rag_project_id: 88 });
    if (path === '/rag-sync/project-map/6') {
      return jsonResponse({ project_id: 6, rag_project_id: 88, rag_project_code: 'R-88', has_api_key: true });
    }
    return jsonResponse({
      sync_log_id: 301,
      sync_type: 'invoice',
      status: 'SUCCESS',
      total_extracted: 2,
      total_imported: 2,
      total_pending: 0,
      imported_ids: [41, 42],
      pending_ids: [],
      errors: [],
    });
  }, async () => {
    const saved = await saveProjectRagMap({ projectId: 6, ragProjectId: 88, ragProjectCode: 'R-88' });
    assert.deepEqual(saved, { projectId: 6, ragProjectId: 88 });
    const result = await syncRagType({ projectId: 6, ragProjectId: 88, syncType: 'invoice' });
    assert.equal(result.syncLogId, 301);
    assert.equal(result.totalImported, 2);
  });
  assert.deepEqual(requests[0], {
    path: '/rag-sync/project-map',
    body: { project_id: 6, rag_project_id: 88, rag_project_code: 'R-88', note: '' },
  });
  assert.equal('rag_api_key' in requests[0].body, false);
  assert.equal('api_key' in requests[0].body, false);
});

test('syncRagBatch preserves backend result states and errors', async () => {
  await withMockFetch(async (_input, _init) => jsonResponse({
    project_id: 6,
    results: [
      {
        sync_log_id: 401,
        sync_type: 'payment',
        status: 'PENDING_REVIEW',
        total_extracted: 3,
        total_imported: 1,
        total_pending: 2,
        imported_ids: [9],
        pending_ids: [10, 11],
        errors: ['主体需人工复核'],
      },
      {
        // A batch-level exception occurs before _do_sync creates a SyncLog;
        // the backend uses zero as that failure-only sentinel.
        sync_log_id: 0,
        sync_type: 'tax_payment',
        status: 'FAILED',
        total_extracted: 0,
        total_imported: 0,
        total_pending: 0,
        imported_ids: [],
        pending_ids: [],
        errors: ['RAG 超时'],
      },
    ],
  }), async () => {
    const result = await syncRagBatch({ projectId: 6, ragProjectId: 88, syncTypes: ['payment', 'tax_payment'] });
    assert.equal(result.results[0].status, 'PENDING_REVIEW');
    assert.equal(result.results[0].syncLogId, 401);
    assert.deepEqual(result.results[0].pendingIds, [10, 11]);
    assert.equal(result.results[1].syncLogId, 0);
    assert.deepEqual(result.results[1].errors, ['RAG 超时']);
  });
});

test('RAG sync parser rejects invalid IDs, statuses, types, and incomplete failures', async () => {
  const expectInvalidBatchResult = async (result: Record<string, unknown>) => {
    await withMockFetch(async () => jsonResponse({ project_id: 6, results: [result] }), async () => {
      await assert.rejects(
        () => syncRagBatch({ projectId: 6, ragProjectId: 88, syncTypes: ['payment', 'tax_payment'] }),
        error => error instanceof Error
          && 'status' in error
          && (error as { status: number }).status === 502,
      );
    });
  };

  await expectInvalidBatchResult({
    sync_log_id: 0,
    sync_type: 'payment',
    status: 'SUCCESS',
    errors: [],
  });
  await expectInvalidBatchResult({
    sync_log_id: -1,
    sync_type: 'payment',
    status: 'SUCCESS',
    errors: [],
  });
  await expectInvalidBatchResult({
    sync_log_id: 1.5,
    sync_type: 'payment',
    status: 'SUCCESS',
    errors: [],
  });
  await expectInvalidBatchResult({
    sync_log_id: 0,
    sync_type: 'payment',
    status: 'FAILED',
  });
  await expectInvalidBatchResult({
    sync_type: 'payment',
    status: 'FAILED',
    errors: ['RAG timeout'],
  });
  await expectInvalidBatchResult({
    sync_log_id: 1,
    sync_type: 'payment',
    status: 'UNKNOWN',
    errors: [],
  });
  await expectInvalidBatchResult({
    sync_log_id: 1,
    sync_type: 'unknown',
    status: 'SUCCESS',
    errors: [],
  });

  await withMockFetch(async () => jsonResponse({
    sync_log_id: 0,
    sync_type: 'invoice',
    status: 'FAILED',
    errors: ['RAG timeout'],
  }), async () => {
    // Zero is a batch-only sentinel; a single-type sync still requires a real
    // positive SyncLog ID.
    await assert.rejects(
      () => syncRagType({ projectId: 6, ragProjectId: 88, syncType: 'invoice' }),
      error => error instanceof Error
        && 'status' in error
        && (error as { status: number }).status === 502,
    );
  });
});

test('fetchTaxLedger maps only real backend items and keeps four-flow checks fail-closed', async () => {
  await withMockFetch(async (input, _init) => {
    assert.equal(String(input), '/api/tax-ledger?project_id=6&page_size=100');
    return jsonResponse({
      status: 'READY',
      message: '',
      items: [{
        id: 17,
        entity_code: 'A01',
        entity_name: '真实主体',
        business_role: '施工',
        revenue: '100.5',
        vat_payable: '2.5',
        period: '2026-08',
        status: '已生成',
        riskLevel: '高危',
        fourFlowsCheck: { contractMatch: true },
      }],
      total: 1,
      has_more: false,
    });
  }, async () => {
    const result = await fetchTaxLedger(6);
    assert.equal(result.items[0].declareAmount, 100.5);
    assert.equal(result.items[0].taxAmount, 2.5);
    assert.equal(result.items[0].fourFlowsCheck.contractMatch, true);
    assert.equal(result.items[0].fourFlowsCheck.invoiceMatch, false);
  });
});

test('TaxLedgerView keeps a successful empty ledger actionable and preserves Tax status errors', () => {
  const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8');
  const ledgerView = readFileSync(new URL('./components/TaxLedgerView.tsx', import.meta.url), 'utf8');

  assert.match(app, /<TaxLedgerView[^>]+dataStatus=\{taxLedgerStatus\}/);
  assert.match(app, /dataStatusMessage=\{taxLedgerStatusMessage\}/);
  assert.match(ledgerView, /if \(dataStatus !== 'READY'\)/);
  assert.match(ledgerView, /message=\{dataStatusMessage\}/);
  assert.match(ledgerView, /onClick=\{onOpenNewRecordModal\}/);
  assert.match(ledgerView, /TAX_LEDGER_EMPTY_MESSAGE/);
  assert.match(ledgerView, /tax-ledger-rebuild-year/);
  assert.match(ledgerView, /tax-ledger-rebuild-month/);
  assert.match(ledgerView, /window\.confirm/);
  assert.match(ledgerView, /原子替换该期间汇总/);
  assert.match(ledgerView, /void onRebuildTaxLedger\(rebuildPeriod\)/);
  assert.match(app, /rebuildTaxLedger\(period\)/);
  assert.match(app, /已生成 \$\{result\.rowCount\} 条/);
  assert.match(app, /已保留当前页面最后可信数据/);
  assert.equal(ledgerView.includes("dataStatus === 'READY' ? 'UNAVAILABLE'"), false);
  assert.match(TAX_LEDGER_EMPTY_MESSAGE, /接口正常、指定期间暂无已生成台账/);
  assert.match(TAX_LEDGER_EMPTY_MESSAGE, /RAG 凭证同步\/结构化入库/);
  assert.match(TAX_LEDGER_EMPTY_MESSAGE, /受控确定性重建生成/);
});

test('rebuildTaxLedger validates the month, requires Tax CSRF, and parses the atomic result', async () => {
  const previousDocument = (globalThis as { document?: unknown }).document;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { cookie: 'tax_csrf=csrf%20token' } });
  const requests: Array<{ path: string; init?: RequestInit }> = [];
  try {
    await withMockFetch(async (input, init) => {
      requests.push({ path: String(input), init });
      return jsonResponse({ status: 'READY', period: '2026-08', row_count: 3 });
    }, async () => {
      const result = await rebuildTaxLedger(' 2026-08 ');
      assert.deepEqual(result, { status: 'READY', period: '2026-08', rowCount: 3 });
    });
  } finally {
    if (previousDocument === undefined) Reflect.deleteProperty(globalThis, 'document');
    else Object.defineProperty(globalThis, 'document', { configurable: true, value: previousDocument });
  }
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/tax-ledger/rebuild');
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), { period: '2026-08' });
  assert.equal((requests[0].init?.headers as Record<string, string>)['X-CSRF-Token'], 'csrf token');
});

test('rebuildTaxLedger fails closed before POST for invalid month or missing CSRF', async () => {
  const previousDocument = (globalThis as { document?: unknown }).document;
  let requestCount = 0;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { cookie: '' } });
  try {
    await withMockFetch(async () => {
      requestCount += 1;
      return jsonResponse({ status: 'READY', period: '2026-08', row_count: 3 });
    }, async () => {
      await assert.rejects(() => rebuildTaxLedger('2026-13'), error => error instanceof ApiError && error.status === 400);
      await assert.rejects(() => rebuildTaxLedger('2026-08'), error => error instanceof ApiError && error.status === 0);
    });
  } finally {
    if (previousDocument === undefined) Reflect.deleteProperty(globalThis, 'document');
    else Object.defineProperty(globalThis, 'document', { configurable: true, value: previousDocument });
  }
  assert.equal(requestCount, 0);
});

test('rebuildTaxLedger rejects an invalid result envelope', async () => {
  const previousDocument = (globalThis as { document?: unknown }).document;
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { cookie: 'tax_csrf=token' } });
  try {
    await withMockFetch(async () => jsonResponse({ status: 'READY', period: '2026-08', row_count: -1 }), async () => {
      await assert.rejects(
        () => rebuildTaxLedger('2026-08'),
        error => error instanceof ApiError && error.status === 502,
      );
    });
  } finally {
    if (previousDocument === undefined) Reflect.deleteProperty(globalThis, 'document');
    else Object.defineProperty(globalThis, 'document', { configurable: true, value: previousDocument });
  }
});

test('fetchRiskEvents validates the collection and maps real backend fields', async () => {
  await withMockFetch(async (input, _init) => {
    assert.equal(String(input), '/api/risks?project_id=6&page_size=100');
    return jsonResponse({
      status: 'READY',
      message: '',
      items: [
        {
          id: 17,
          project_name: '真实项目',
          entity_name: '真实主体',
          risk_type: '缺少发票证据',
          severity: '高危',
          trigger_time: '2026-08-25',
          description: '后端风险描述',
          audit_suggestions: '补充凭证',
          status: '处置中',
          handler: '责任人',
        },
        { project_name: '没有 ID 的风险' },
        { id: 18, severity: 'UNKNOWN', status: 'UNKNOWN' },
      ],
      total: 3,
      has_more: false,
    });
  }, async () => {
    const result = await fetchRiskEvents(6);
    assert.equal(result.status, 'READY');
    assert.equal(result.items.length, 2);
    assert.equal(result.items[0].projectName, '真实项目');
    assert.equal(result.items[0].severity, '高危');
    assert.equal(result.items[0].status, '处置中');
    assert.equal(result.items[1].severity, '轻度');
    assert.equal(result.items[1].status, '待处置');
  });
});

test('fetchRiskEvents preserves a successful empty collection as READY', async () => {
  await withMockFetch(async () => jsonResponse({
    status: 'READY', message: '暂无已识别风险事件', items: [], total: 0, has_more: false,
  }), async () => {
    const result = await fetchRiskEvents(6);
    assert.equal(result.status, 'READY');
    assert.deepEqual(result.items, []);
    assert.equal(result.message, '暂无已识别风险事件');
  });
});

test('fetchRiskEvents throws ApiError for an invalid collection envelope', async () => {
  await withMockFetch(async () => jsonResponse({ status: 'READY', items: null }), async () => {
    await assert.rejects(
      () => fetchRiskEvents(6),
      error => error instanceof ApiError
        && error.status === 502
        && /风险接口返回格式不完整/.test(error.message),
    );
  });
});

test('reviewed RAG contracts are read and confirmed through fixed Tax endpoints only', async () => {
  const requests: Array<{ path: string; body?: unknown }> = [];
  await withMockFetch(async (input, init) => {
    requests.push({ path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (String(input).startsWith('/rag-sync/pending?')) {
      return jsonResponse({
        page: 1, page_size: 20, total: 1,
        items: [{
          id: 41, project_id: 6, sync_type: 'contract', source_chunk_id: 7001,
          filename: '外部供货合同.pdf', page_start: 3, confidence: 1,
          review: {
            reason: '外部交易方 tax_id 未登记',
            party_a_name: '成都建工', party_a_tax_id: '91510100A08',
            party_b_name: '供货商', party_b_tax_id: '91510400EXT',
          },
        }],
      });
    }
    return jsonResponse({
      ok: true, pending_id: 41, record_id: 91,
      created_external_parties: [{ id: 12, code: 'EXT-123', name: '供货商', tax_id: '91510400EXT' }],
    });
  }, async () => {
    const pending = await fetchRagPendingContracts(6);
    assert.deepEqual(pending[0].partyB, { name: '供货商', taxId: '91510400EXT' });
    const result = await confirmRagPendingContractAndCreateParties(41);
    assert.equal(result.recordId, 91);
    assert.equal(result.createdExternalParties[0].code, 'EXT-123');
  });
  assert.deepEqual(requests, [
    { path: '/rag-sync/pending?project_id=6&sync_type=contract&status=pending', body: undefined },
    { path: '/rag-sync/pending/41/confirm-contract-and-create-parties', body: { confirm: true } },
  ]);
});

test('fetchProjectCounterparties maps canonical and external rows from the real backend', async () => {
  await withMockFetch(async () => jsonResponse({
    status: 'READY',
    message: '',
    total: 2,
    items: [
      {
        party_code: 'A08', party_name: '四川锐宝建设有限公司',
        kind: 'entity', isInternal: true, source: 'entities',
        contract_count: 1, contract_amount: 1200000,
        invoice_in_count: 0, invoice_in_net: 0, invoice_in_vat: 0,
        invoice_out_count: 2, invoice_out_net: 200000, invoice_out_vat: 18000,
        cashflow_in_count: 1, cashflow_in_amount: 18000000,
        cashflow_out_count: 1, cashflow_out_amount: 900000,
        real_cost_count: 0, real_cost_amount: 0,
        fulfillment_count: 0, fulfillment_amount: 0,
      },
      {
        party_code: 'EXT-TF-001', party_name: '成都土方供应有限公司',
        kind: 'external', isInternal: false, source: 'external_parties',
        contract_count: 1, contract_amount: 1200000,
        invoice_in_count: 1, invoice_in_net: 100000, invoice_in_vat: 9000,
        invoice_out_count: 0, invoice_out_net: 0, invoice_out_vat: 0,
        cashflow_in_count: 0, cashflow_in_amount: 0,
        cashflow_out_count: 1, cashflow_out_amount: 90000,
        real_cost_count: 1, real_cost_amount: 80000,
        fulfillment_count: 1, fulfillment_amount: 80000,
      },
    ],
  }), async () => {
    const result = await fetchProjectCounterparties(1);
    assert.equal(result.status, 'READY');
    assert.equal(result.items.length, 2);
    assert.equal(result.total, 2);

    const byCode = Object.fromEntries(result.items.map(p => [p.partyCode, p]));
    const a08 = byCode['A08'];
    assert.equal(a08.isInternal, true);
    assert.equal(a08.source, 'entities');
    assert.equal(a08.kind, 'entity');
    assert.equal(a08.contractAmount, 1200000);
    assert.equal(a08.invoiceOutVat, 18000);

    const ext = byCode['EXT-TF-001'];
    assert.equal(ext.isInternal, false);
    assert.equal(ext.source, 'external_parties');
    assert.equal(ext.kind, 'external');
    assert.equal(ext.partyName, '成都土方供应有限公司');
    assert.equal(ext.invoiceInVat, 9000);
    assert.equal(ext.realCostAmount, 80000);
    assert.equal(ext.fulfillmentAmount, 80000);
  });
});

test('fetchProjectCounterparties rejects an envelope without items', async () => {
  await withMockFetch(async () => jsonResponse({ status: 'READY' }), async () => {
    await assert.rejects(
      () => fetchProjectCounterparties(1),
      error => error instanceof ApiError
        && error.status === 502
        && /对手方接口返回格式不完整/.test(error.message),
    );
  });
});

test('fetchProjectCounterparties validates project id', async () => {
  await assert.rejects(
    () => fetchProjectCounterparties(0),
    error => error instanceof ApiError && error.status === 400,
  );
});
