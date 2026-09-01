import { ApiError } from './api';
import { ProjectItem, ProjectRagMapping, RagProjectCandidate } from './types';

function normalized(value: string | undefined): string {
  return (value || '').trim().toLocaleLowerCase();
}

export function mappingMismatchWarning(
  taxProjectCode: string | undefined,
  taxProjectName: string | undefined,
  candidate: RagProjectCandidate | undefined,
): string | null {
  if (!candidate) return null;
  const taxCode = normalized(taxProjectCode);
  const taxName = normalized(taxProjectName);
  const ragCode = normalized(candidate.projectCode);
  const ragName = normalized(candidate.name);
  const codeMismatch = Boolean(taxCode && ragCode && taxCode !== ragCode);
  const nameMismatch = Boolean(taxName && ragName && taxName !== ragName);
  if (codeMismatch && nameMismatch) return '警告：该 RAG 候选的项目编号和名称均与当前 Tax 项目不一致，请确认这是有意的跨项目映射。';
  if (codeMismatch) return '提示：该 RAG 候选的项目编号与当前 Tax 项目不一致，请确认这是有意的跨编号映射。';
  if (nameMismatch) return '提示：该 RAG 候选的项目名称与当前 Tax 项目不一致，请确认这是有意的跨名称映射。';
  return null;
}

export function mappingChanged(
  mapping: ProjectRagMapping | null,
  candidate: RagProjectCandidate | undefined,
): boolean {
  return Boolean(candidate && (!mapping || candidate.id !== mapping.ragProjectId));
}

export function ragErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const status = error.status > 0 ? `（HTTP ${error.status}）` : '';
    const message = error.message || fallback;
    return status && message.includes(`HTTP ${error.status}`) ? message : `${message}${status}`;
  }
  return error instanceof Error ? error.message : fallback;
}

export function canSaveRagMapping(input: {
  statusOk: boolean;
  candidates: RagProjectCandidate[];
  selected: RagProjectCandidate | undefined;
  mapping: ProjectRagMapping | null;
  editing: boolean;
  busy: boolean;
}): boolean {
  return input.statusOk
    && input.candidates.length > 0
    && Boolean(input.selected)
    && (input.editing || !input.mapping)
    && mappingChanged(input.mapping, input.selected)
    && !input.busy;
}

export function canSyncRagMapping(input: {
  statusOk: boolean;
  candidates: RagProjectCandidate[];
  mapping: ProjectRagMapping | null;
  mappingCandidate: RagProjectCandidate | undefined;
  mappingVerified: boolean;
  editing: boolean;
  selectedTypes: number;
  busy: boolean;
}): boolean {
  return input.statusOk
    && input.candidates.length > 0
    && Boolean(input.mapping && input.mappingCandidate)
    && input.mappingVerified
    && !input.editing
    && input.selectedTypes > 0
    && !input.busy;
}

export function operationErrorTitle(kind: 'status' | 'mapping' | 'save' | 'sync'): string {
  switch (kind) {
    case 'status': return 'RAG 状态读取失败';
    case 'mapping': return 'Tax 项目映射读取失败';
    case 'save': return 'RAG 映射保存失败';
    case 'sync': return 'RAG 凭证同步失败';
  }
}

export function resolveTaxProjectId(
  projects: ProjectItem[],
  preferredProjectId?: number,
  currentProjectId = '',
): string {
  if (currentProjectId && projects.some(project => String(project.numericId) === currentProjectId)) return currentProjectId;
  if (preferredProjectId && projects.some(project => project.numericId === preferredProjectId)) return String(preferredProjectId);
  return projects[0]?.numericId ? String(projects[0].numericId) : '';
}

export function activeTaxProjectId(
  projects: ProjectItem[],
  selectedProjectId: string,
  fallbackProjectId?: number,
): number | undefined {
  const selected = projects.find(project => String(project.numericId) === selectedProjectId)?.numericId;
  if (selected && Number.isInteger(selected) && selected > 0) return selected;
  return fallbackProjectId && Number.isInteger(fallbackProjectId) && fallbackProjectId > 0 ? fallbackProjectId : undefined;
}
