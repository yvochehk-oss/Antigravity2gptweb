/**
 * 建筑工程财务与税务风控管控系统 - 类型定义
 * 全中文业务术语体系
 */

// 数据状态。没有真实接口结果时，界面必须显式标记不可用，不能用空对象或演示数字伪装成功。
export type DataStatus = 'LOADING' | 'READY' | 'DEGRADED' | 'UNAVAILABLE';

/** Safe AI dependency health returned by the Tax service `/healthz` endpoint. */
export interface AiModelEndpointHealth {
  name: string;
  status: 'ok' | 'degraded' | 'down';
}

/** UI-ready AI model-pool state; it is always derived from a real response. */
export interface AiModelStatus {
  state: DataStatus;
  message: string;
  endpoints: AiModelEndpointHealth[];
}

// Tax -> RAG 凭证同步只允许使用后端声明的四类记录。不要在前端扩展为
// 自由文本，否则后端确定性映射会被绕过。
export type RagSyncType = 'invoice' | 'contract' | 'payment' | 'tax_payment';

export interface RagProjectCandidate {
  id: number;
  projectCode: string;
  name: string;
  status?: string;
}

export interface RagStatusResponse {
  ok: boolean;
  ragVersion: string;
  llmExtraction: boolean;
  projects: RagProjectCandidate[];
  error: string;
}

/**
 * Safe metadata for the server-side Tax -> RAG connection.
 *
 * The shared credential is deliberately absent: it is process supplied on
 * the Tax server and must never enter browser state or a browser request.
 */
export interface RagServiceSettings {
  ok: boolean;
  url: string;
  host: string;
  approvedPrivate: boolean;
  configured: boolean;
  lastTestedAt: string;
  ragVersion: string;
  llmExtraction: boolean;
  projects: RagProjectCandidate[];
  error: string;
}

export interface ProjectRagMapping {
  projectId: number;
  ragProjectId: number;
  ragProjectCode: string;
  ragUrl?: string;
  hasApiKey: boolean;
  note?: string;
  syncedAt?: string;
}

// Tax deterministic matching completeness. This is evidence availability,
// not a project-health score; the UI must never label percentage as a score.
export type MatchingCompletenessStatus = 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE';

export interface MatchingFlowCount {
  expected: number;
  available: number;
  missing: number;
}

export interface MatchingCompletenessCounts {
  rows: number;
  expectedEvidence: number;
  availableEvidence: number;
  missingEvidence: number;
  byFlow: Record<string, MatchingFlowCount>;
}

export interface ProjectMatchingCompleteness {
  projectId: number;
  status: MatchingCompletenessStatus;
  percentage: number | null;
  score: null;
  counts: MatchingCompletenessCounts;
  dataGaps: string[];
  updated: string;
  source: string;
}

export interface MatchingCompletenessSummary {
  status: MatchingCompletenessStatus;
  percentage: number | null;
  dataGaps: string[];
}

export type RagSyncStatus = 'SUCCESS' | 'PENDING_REVIEW' | 'PARTIAL' | 'FAILED' | 'RUNNING';

export interface RagSyncResult {
  syncLogId: number;
  syncType: RagSyncType;
  status: RagSyncStatus | string;
  totalExtracted: number;
  totalImported: number;
  totalPending: number;
  importedIds: number[];
  pendingIds: number[];
  errors: string[];
}

export interface RagSyncBatchResponse {
  projectId: number;
  results: RagSyncResult[];
}

export interface RagSyncPendingContract {
  id: number;
  projectId: number;
  sourceChunkId: number;
  filename: string;
  pageStart: number | null;
  confidence: number;
  reason: string;
  partyA: { name: string; taxId: string };
  partyB: { name: string; taxId: string };
}

export interface RagConfirmedExternalParty {
  id: number;
  code: string;
  name: string;
  taxId: string | null;
}

export interface RagConfirmPendingContractResult {
  pendingId: number;
  recordId: number;
  createdExternalParties: RagConfirmedExternalParty[];
}

// 风险等级
export type RiskLevel = '正常' | '预警' | '高危' | '未知';

// 税种类别
// 税种名称来自后端主数据，前端不应限制为演示数据中的少数值。
export type TaxCategory = string;

// 申报状态
export type FilingStatus = string;

// 税务台账单条记录

export interface EntityTaxLedgerRecord {
  id: string;
  period: string;
  entityCode: string;
  entityName: string;
  businessRole: string;
  legalEntity: boolean;

  outputVat: number;
  inputVat: number;
  vatPayable: number;

  revenue: number;
  realCost: number;
  estimatedProfit: number;
  estimatedCit: number;
  citNote: string;

  generated: boolean;
  dataStatus: DataStatus;
  dataGaps: string[];
  trusted: boolean;
  updateTime: string;
}

export interface ProjectTaxAnalysisRecord {
  projectId: number;
  projectCode: string;
  projectName: string;

  period: string;
  entityCode: string | null;

  outInvoiceNet: number;
  outInvoiceVat: number;
  inInvoiceNet: number;
  inInvoiceVat: number;
  deductibleInputVat: number;
  realCost: number;
  invoiceCount: number;

  sourceOfTruth: string;
  legacyTablesUsed: boolean;
  realCostBasis: string;
  dataGaps: string[];
}

export interface TaxLedgerRecord {
  id: string;
  entityName: string;         // 所属实体/工程标段 (如：甲实体-基础工程)
  entityCategory: string;     // 实体分类 (如：土建承包、物资供应、劳务分包、设备安装)
  isInternal?: boolean;       // 是否系统内关联企业 (true: 系统内, false: 系统外)
  source?: string;            // 归属来源 (系统内 / 系统外)
  declareAmount: number;      // 申报金额 (元)
  taxAmount: number;          // 应纳税额 (元)
  taxCategory: TaxCategory;   // 税种类别
  filingPeriod: string;       // 纳税所属期 (如：2026年第2季度)
  status: FilingStatus;       // 审核状态
  riskLevel: RiskLevel;       // 风险等级
  riskDescription?: string;   // 风险研判说明
  invoiceCode?: string;       // 发票代码/凭证号
  ragSourceDoc?: string;      // RAG 业财知识湖溯源文档
  vectorSimilarity?: number;  // 向量匹配置信度 (0~100)
  fourFlowsCheck: {           // 四流合一核验 (合同/发票/资金/物资)
    contractMatch: boolean;
    invoiceMatch: boolean;
    paymentMatch: boolean;
    logisticsMatch: boolean;
  };
  updateTime: string;         // 更新时间
}

// 成本分解科目 (工作任务分解结构)
export interface CostBreakdownItem {
  id: string;
  code: string;               // 科目编码 (如：01.1)
  name: string;               // 科目名称 (如：勘察与深化设计)
  level: number;              // 层级 (1级或2级)
  plannedAmount: number;      // 计划预算 (元)
  actualAmount: number;       // 实际发生金额 (元)
  variancePercent: number;    // 偏差率 (百分比)
  status: '正常推进' | '节约支出' | '超支预警' | '待发生';
  manager: string;            // 责任工程师/财务负责人
  notes?: string;             // 备注说明
}

// 项目总览信息
export interface ProjectItem {
  id: string;
  numericId: number;
  projectCode: string;        // 工程编号 (如：工号-2023-014)
  name: string;               // 项目名称 (如：国家体育场二期改扩建)
  constructionStage: string;  // 建设阶段 (如：主体结构施工阶段)
  healthGrade: string;        // 综合健康评级 (如：甲级·A-、乙级·B+)
  totalBudget: number;        // 已批总预算 (元)
  spentAmount: number;        // 累计已付款项 (元)
  remainingBudget: number;    // 剩余可用预算 (元)
  progressPercent: number;    // 资金消耗/形象进度 (百分比)
  taxRiskGrade: '极低' | '低' | '中等偏高' | '高危' | '未知'; // 税务风险评级
  isOverBudget: boolean | null; // 后端未提供预算口径时为 null
  managerName: string;        // 项目经理
  location: string;           // 项目所在地
  teamAvatars: string[];      // 团队成员头像
  taxRecords: TaxLedgerRecord[]; // 关联税务台账
  costItems: CostBreakdownItem[];// 关联成本分解
}

// 风险预警事件
export interface RiskEvent {
  id: string;
  projectName: string;
  entityName: string;
  riskType: string;
  severity: '高危' | '中度' | '轻度';
  triggerTime: string;
  description: string;
  auditSuggestions: string;
  status: '待处置' | '处置中' | '已闭环' | '已上报管理层';
  handler: string;
}

// 智能对话消息
export interface AssistantMessage {
  id: string;
  sender: 'ai' | 'user' | 'system';
  content: string;
  timestamp: string;
  suggestedActions?: string[];
  referenceData?: string;
  isThinking?: boolean;
  /** Metadata returned by the backend model pool; never inferred in the UI. */
  aiMetadata?: AiExecutionMetadata;
}

export interface AiEndpointMetadata {
  id?: number;
  name?: string;
  model?: string;
}

export interface AiAttemptSummary {
  endpoint?: AiEndpointMetadata;
  status?: string;
  error?: string;
}

/** Optional execution metadata. The backend may use selected/effective naming. */
export interface AiExecutionMetadata {
  status?: string;
  degraded?: boolean;
  fallback?: boolean;
  selectedEndpoint?: AiEndpointMetadata;
  effectiveEndpoint?: AiEndpointMetadata;
  attempts?: AiAttemptSummary[];
  [key: string]: unknown;
}

// 审计追溯底稿记录
export interface AuditTrailRecord {
  id: string;
  timestamp: string;
  operator: string;
  role: string;
  targetSubject: string;
  actionType: string;
  details: string;
  integrityHash: string;      // 防篡改校验码
}

// 全局系统运行参数配置
export interface SystemSettings {
  autoFourFlowsMatch: boolean;          // 自动启动“四流合一”交叉比对
  crossRegionTaxThreshold: number;      // 跨区施工异地预缴核销偏差阈值 (%)
  budgetOverrunStopPayThreshold: number;// 超概算阈值自动触发止付令 (%)
  taxAuditAutoNotify: boolean;          // 高危涉税风险实时告警通知
  dataRefreshInterval: number;          // 数据自动同步频率 (秒，如 30, 60, 300, 0表示手动)
  aiDeepAnalysisMode: boolean;          // AI智能助手深度穿透核验模式
}
