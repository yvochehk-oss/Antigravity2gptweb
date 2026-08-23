/**
 * 建筑工程财务与税务风控管控系统 - 类型定义
 * 全中文业务术语体系
 */

// 风险等级
export type RiskLevel = '正常' | '预警' | '高危';

// 税种类别
export type TaxCategory = 
  | '增值税 (普通/专用)'
  | '企业所得税'
  | '个人所得税 (全员扣缴)'
  | '城市维护建设税及附加'
  | '印花税与环境保护税'
  | '房产税与城镇土地使用税';

// 申报状态
export type FilingStatus = 
  | '已审计核销'
  | '待主管复核'
  | '异常-税务稽查中'
  | '已完税核销'
  | '已合规申报'
  | '已暂扣待缴'
  | '已发起退税申请';

// 税务台账单条记录
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
  projectCode: string;        // 工程编号 (如：工号-2023-014)
  name: string;               // 项目名称 (如：国家体育场二期改扩建)
  constructionStage: string;  // 建设阶段 (如：主体结构施工阶段)
  healthGrade: string;        // 综合健康评级 (如：甲级·A-、乙级·B+)
  totalBudget: number;        // 已批总预算 (元)
  spentAmount: number;        // 累计已付款项 (元)
  remainingBudget: number;    // 剩余可用预算 (元)
  progressPercent: number;    // 资金消耗/形象进度 (百分比)
  taxRiskGrade: '极低' | '低' | '中等偏高' | '高危'; // 税务风险评级
  isOverBudget: boolean;      // 是否超支
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
  riskType: '跨区预缴与个税核销争议' | '预提所得税争议' | '进销项不匹配' | '合同四流背离' | '预算严重超支' | '未开票挂账过大';
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
}

// 审计追溯底稿记录
export interface AuditTrailRecord {
  id: string;
  timestamp: string;
  operator: string;
  role: string;
  targetSubject: string;
  actionType: '凭证修改' | '税务核销' | '预算调整' | '风险标记' | '合规复核' | '报表签批' | '系统设置变更' | 'RAG底账同步' | '四流智能比对';
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

