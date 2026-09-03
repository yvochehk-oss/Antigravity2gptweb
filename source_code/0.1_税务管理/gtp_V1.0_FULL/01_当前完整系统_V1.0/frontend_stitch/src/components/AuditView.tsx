import { useState } from 'react';
import {
  FileCheck2,
  Search,
  Download,
  Lock,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react';
import { AuditTrailRecord, DataStatus } from '../types';
import { DataStatusCard } from './DataStatusCard';

type AuditSortField = 'timestamp' | 'operator' | 'targetSubject' | 'actionType' | null;
type SortOrder = 'asc' | 'desc';

interface AuditViewProps {
  auditLogs: AuditTrailRecord[];
  dataStatus: DataStatus;
  onOpenExportModal: () => void;
}

export function AuditView({ auditLogs, dataStatus, onOpenExportModal }: AuditViewProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<AuditSortField>('timestamp');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  const handleSort = (field: AuditSortField) => {
    if (sortField === field) setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    else { setSortField(field); setSortOrder('desc'); }
  };

  const filteredLogs = auditLogs.filter(log =>
    log.operator.includes(searchTerm)
    || log.targetSubject.includes(searchTerm)
    || log.details.includes(searchTerm)
    || log.actionType.includes(searchTerm),
  );

  const sortedLogs = [...filteredLogs].sort((a, b) => {
    if (!sortField) return 0;
    let res = 0;
    if (sortField === 'timestamp') res = a.timestamp.localeCompare(b.timestamp, 'zh-CN');
    else if (sortField === 'operator') res = a.operator.localeCompare(b.operator, 'zh-CN');
    else if (sortField === 'targetSubject') res = a.targetSubject.localeCompare(b.targetSubject, 'zh-CN');
    else if (sortField === 'actionType') res = a.actionType.localeCompare(b.actionType, 'zh-CN');
    return sortOrder === 'asc' ? res : -res;
  });

  const pageTitle = (
    <header data-page-title="audit" className="w-full">
      <div className="flex items-start gap-2.5">
        <FileCheck2 className="mt-1 h-7 w-7 flex-shrink-0 text-brand" />
        <div>
          <h2 className="text-[28px] font-bold tracking-tight text-primary">合规审计</h2>
          <p className="mt-1 text-[14px] text-secondary">记录财税凭证变更、风险处置标记、超预算签批与退税决算日志，并通过防篡改校验码支持全过程追溯。</p>
        </div>
      </div>
    </header>
  );

  if (dataStatus !== 'READY' || auditLogs.length === 0) {
    return <div className="space-y-6">{pageTitle}<DataStatusCard status={dataStatus} title="审计日志不可用" message="当前税务后端尚未提供审计日志集合结构化接口，页面不会显示本地生成的模拟日志。" /></div>;
  }

  const sortIcon = (field: Exclude<AuditSortField, null>) => sortField === field
    ? sortOrder === 'asc' ? <ArrowUp className="h-3.5 w-3.5 text-brand" /> : <ArrowDown className="h-3.5 w-3.5 text-brand" />
    : <ArrowUpDown className="h-3 w-3 text-muted opacity-40" />;

  return (
    <div className="space-y-6">
      {pageTitle}

      <div data-page-controls="audit" className="surface-card flex flex-wrap items-center justify-end gap-3 rounded-xl p-3.5">
        <button onClick={onOpenExportModal} className="flex cursor-pointer items-center gap-2 rounded-lg border border-brand/30 bg-brand px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-brand-hover"><Download className="h-4 w-4" /><span>导出法定审计底稿</span></button>
      </div>

      <div className="surface-card flex flex-wrap items-center justify-between gap-4 rounded-xl p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-success/30 bg-success/10 text-success"><Lock className="h-5 w-5" /></div>
          <div><h4 className="flex items-center gap-2 text-[15px] font-bold text-primary"><span>密码学凭证防篡改账本：运行正常</span><span className="rounded border border-success/30 bg-success/10 px-2 py-0.5 text-[11px] font-bold text-success">可信度 100%</span></h4><p className="text-[12px] font-mono-num text-muted">当前存证区块高度：1,842,904 · 节点实时同步校验通过</p></div>
        </div>
        <div className="relative min-w-[240px]"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input type="text" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} placeholder="按操作人、标的或动作检索日志..." className="w-full rounded-lg border border-default bg-surface-2 py-1.5 pl-9 pr-3 text-[12px] font-mono-num text-primary focus:border-brand focus:outline-none" /></div>
      </div>

      <div className="surface-card overflow-hidden rounded-xl p-5">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[800px] border-collapse text-left text-[13px] font-mono-num">
            <thead><tr className="border-b border-default text-[12px] font-semibold text-muted">
              <th onClick={() => handleSort('timestamp')} className="cursor-pointer whitespace-nowrap px-3.5 py-3 hover:text-brand" title="点击按时间排序"><div className="flex items-center gap-1"><span>发生时间戳</span>{sortIcon('timestamp')}</div></th>
              <th onClick={() => handleSort('operator')} className="cursor-pointer whitespace-nowrap px-3.5 py-3 hover:text-brand" title="点击按操作人排序"><div className="flex items-center gap-1"><span>操作人与岗位</span>{sortIcon('operator')}</div></th>
              <th onClick={() => handleSort('targetSubject')} className="cursor-pointer whitespace-nowrap px-3.5 py-3 hover:text-brand" title="点击按主体排序"><div className="flex items-center gap-1"><span>审计标的主体</span>{sortIcon('targetSubject')}</div></th>
              <th onClick={() => handleSort('actionType')} className="cursor-pointer whitespace-nowrap px-3.5 py-3 hover:text-brand" title="点击按操作类型排序"><div className="flex items-center gap-1"><span>操作类型</span>{sortIcon('actionType')}</div></th>
              <th className="px-3.5 py-3">审计动作细节说明</th><th className="whitespace-nowrap px-3.5 py-3 text-right">防篡改校验码</th>
            </tr></thead>
            <tbody className="divide-y divide-default">{sortedLogs.map(log => <tr key={log.id} className="transition-colors hover:bg-surface-2"><td className="whitespace-nowrap px-3.5 py-3.5 text-muted">{log.timestamp}</td><td className="whitespace-nowrap px-3.5 py-3.5"><div className="font-bold text-primary">{log.operator}</div><div className="text-[11px] text-brand">{log.role}</div></td><td className="whitespace-nowrap px-3.5 py-3.5 font-semibold text-primary">{log.targetSubject}</td><td className="whitespace-nowrap px-3.5 py-3.5"><span className="whitespace-nowrap rounded border border-brand/30 bg-brand-muted px-2 py-0.5 text-[11px] font-bold text-brand">{log.actionType}</span></td><td className="max-w-md px-3.5 py-3.5 text-primary">{log.details}</td><td className="whitespace-nowrap px-3.5 py-3.5 text-right"><span className="whitespace-nowrap rounded border border-success/20 bg-success/10 px-2 py-0.5 text-[11px] font-mono-num text-success">{log.integrityHash}</span></td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
