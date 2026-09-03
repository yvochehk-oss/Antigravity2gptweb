export function dataStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'LOADING': return '加载中';
    case 'READY': return '就绪';
    case 'DEGRADED': return '降级运行';
    case 'UNAVAILABLE': return '服务不可用';
    case 'EMPTY': return '暂无数据';
    case 'AVAILABLE': return '可用';
    default: return status ? enumLabel(status) : '—';
  }
}

export function periodStateLabel(state: string | null | undefined): string {
  switch (state) {
    case 'OPEN': return '开放期';
    case 'CLOSED': return '已关账';
    case 'REOPENED': return '已重开';
    case 'LOCKED': return '已锁定';
    default: return state ? enumLabel(state) : '—';
  }
}

export function runStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'PENDING': return '待执行';
    case 'RUNNING': return '执行中';
    case 'SUCCESS':
    case 'SUCCEEDED': return '执行成功';
    case 'FAILED': return '执行失败';
    case 'CANCELLED': return '已取消';
    case 'NO_CHANGE': return '无需变更';
    case 'BUILT': return '已生成';
    default: return status ? enumLabel(status) : '—';
  }
}

export function scopeLabel(scope: string | null | undefined): string {
  switch (scope) {
    case 'LEGAL_ENTITY_PROJECTION': return '法人管理／经营投影';
    case 'LEGAL_ENTITY_STATUTORY': return '法人法定申报';
    case 'PROJECT_BOUNDARY': return '项目管理边界';
    case 'CANONICAL_FACTS': return '规范事实';
    default: return scope ? enumLabel(scope) : '—';
  }
}

export function severityLabel(value: string | null | undefined): string {
  switch ((value ?? '').toUpperCase()) {
    case 'CRITICAL': return '严重';
    case 'HIGH': return '高';
    case 'MEDIUM': return '中';
    case 'LOW': return '低';
    default: return value ? enumLabel(value) : '—';
  }
}

export function priorityLabel(value: string | null | undefined): string {
  switch ((value ?? '').toUpperCase()) {
    case 'P0': return '最高优先级';
    case 'P1': return '高优先级';
    case 'P2': return '中优先级';
    case 'P3': return '低优先级';
    default: return value ? enumLabel(value) : '—';
  }
}

export function componentTypeLabel(value: string | null | undefined): string {
  switch ((value ?? '').toUpperCase()) {
    case 'OUTPUT_VAT':
    case 'OUTPUT_VAT_EVENT': return '销项增值税事件';
    case 'INPUT_VAT':
    case 'INPUT_VAT_CLAIM': return '进项增值税抵扣事项';
    case 'TAX_PREPAYMENT': return '税款预缴';
    case 'OPENING_INPUT_CREDIT': return '期初留抵';
    case 'PRIOR_LEDGER': return '上期法定台账';
    case 'OPENING_BALANCE_SEED': return '期初余额种子';
    default: return value ? enumLabel(value) : '未分类组件';
  }
}

export function fieldLabel(value: string): string {
  const known: Record<string, string> = {
    componentType: '组件类型',
    amount: '金额',
    outputVatEventId: '销项增值税事件编号',
    inputVatClaimId: '进项增值税抵扣事项编号',
    taxPrepaymentFactId: '税款预缴事实编号',
    priorLedgerId: '上期法定台账编号',
    openingBalanceSeedId: '期初余额种子编号',
    invoiceFactId: '发票事实编号',
    sourceDocumentId: '来源单据编号',
    overall_risk: '总体风险',
    risk_level: '风险等级',
    score: '综合评分',
    confidence: '置信度',
    status: '状态',
    requires_manual_review: '是否需要人工复核',
  };
  return known[value] ?? enumLabel(value);
}

export function enumLabel(value: string): string {
  const normalized = value.trim();
  if (!normalized) return '—';
  const direct: Record<string, string> = {
    TRUE: '是', FALSE: '否',
    INTERNAL: '系统内', EXTERNAL: '系统外',
    COMPLETED: '已完成', RUNNING: '执行中', PENDING: '排队中', FAILED: '失败',
    NORMAL: '正常', MONTHLY: '月度运行', RESTATEMENT: '更正运行',
  };
  const upper = normalized.toUpperCase();
  if (direct[upper]) return direct[upper];
  return normalized
    .replaceAll('_', ' ')
    .replace(/\bVAT\b/gi, '增值税')
    .replace(/\bCIT\b/gi, '企业所得税')
    .replace(/\bAI\b/gi, '智能')
    .replace(/\bAPI\b/gi, '接口')
    .replace(/\bRUN\b/gi, '运行')
    .replace(/\bREADY\b/gi, '就绪')
    .replace(/\bDEGRADED\b/gi, '降级运行')
    .replace(/\bUNAVAILABLE\b/gi, '服务不可用')
    .replace(/\bPROJECTION\b/gi, '投影')
    .replace(/\bSTATUTORY\b/gi, '法定申报')
    .replace(/\bCANONICAL\b/gi, '规范')
    .trim();
}
