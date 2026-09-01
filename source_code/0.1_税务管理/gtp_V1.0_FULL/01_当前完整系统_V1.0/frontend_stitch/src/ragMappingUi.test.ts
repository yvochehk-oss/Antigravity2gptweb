import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canSaveRagMapping,
  canSyncRagMapping,
  activeTaxProjectId,
  mappingChanged,
  mappingMismatchWarning,
  operationErrorTitle,
  ragErrorMessage,
  resolveTaxProjectId,
} from './ragMappingUi';
import { ApiError } from './api';

const taxCode = 'CD-TF-001';
const taxName = '成都天府项目';

test('mappingChanged requires an explicit new candidate and detects an overwrite', () => {
  assert.equal(mappingChanged(null, undefined), false);
  assert.equal(mappingChanged(null, { id: 1, projectCode: 'YB-001', name: 'RAG 项目' }), true);
  assert.equal(mappingChanged({ projectId: 2, ragProjectId: 1, ragProjectCode: 'YB-001', hasApiKey: false }, { id: 1, projectCode: 'YB-001', name: 'RAG 项目' }), false);
  assert.equal(mappingChanged({ projectId: 2, ragProjectId: 1, ragProjectCode: 'YB-001', hasApiKey: false }, { id: 2, projectCode: 'YB-002', name: '另一个 RAG 项目' }), true);
});

test('mappingMismatchWarning warns on obvious code/name mismatch without blocking it', () => {
  const warning = mappingMismatchWarning(taxCode, taxName, { id: 1, projectCode: 'YB-DEMO-001', name: '演示项目' });
  assert.match(warning ?? '', /编号和名称均与当前 Tax 项目不一致/);
  assert.equal(mappingMismatchWarning(taxCode, taxName, { id: 1, projectCode: taxCode, name: taxName }), null);
  assert.equal(mappingMismatchWarning(taxCode, taxName, undefined), null);
});

test('RAG unavailable or empty candidates fail closed without replacing an existing mapping', () => {
  const mapping = { projectId: 2, ragProjectId: 1, ragProjectCode: 'R-1', hasApiKey: false };
  const candidate = { id: 2, projectCode: 'R-2', name: '另一个 RAG 项目' };
  assert.equal(canSaveRagMapping({ statusOk: false, candidates: [candidate], selected: candidate, mapping, editing: true, busy: false }), false);
  assert.equal(canSaveRagMapping({ statusOk: true, candidates: [], selected: undefined, mapping, editing: true, busy: false }), false);
  assert.equal(canSyncRagMapping({ statusOk: true, candidates: [], mapping, mappingCandidate: undefined, mappingVerified: true, editing: false, selectedTypes: 1, busy: false }), false);
  assert.equal(mapping.ragProjectId, 1);
});

test('RAG operation errors retain their HTTP status and stay semantically separate', () => {
  const error = ragErrorMessage(new ApiError('上游超时', 502), 'RAG 服务不可用');
  assert.match(error, /上游超时/);
  assert.match(error, /HTTP 502/);
  assert.equal(operationErrorTitle('mapping'), 'Tax 项目映射读取失败');
  assert.equal(operationErrorTitle('sync'), 'RAG 凭证同步失败');
});

test('Tax project selection keeps the current choice and resolves the corresponding map ID', () => {
  const projects = [
    { numericId: 11, id: '11', projectCode: 'T-11', name: '项目一' },
    { numericId: 22, id: '22', projectCode: 'T-22', name: '项目二' },
  ] as never[];
  assert.equal(resolveTaxProjectId(projects, 22), '22');
  assert.equal(resolveTaxProjectId(projects, 22, '11'), '11');
  assert.equal(activeTaxProjectId(projects, '22', 11), 22);
  assert.equal(activeTaxProjectId(projects, '', 11), 11);
});
