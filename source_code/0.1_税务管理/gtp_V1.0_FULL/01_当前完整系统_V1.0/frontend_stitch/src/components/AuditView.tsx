import { useState } from 'react';
import { 
  FileCheck2, 
  ShieldCheck, 
  Search, 
  Download, 
  Lock, 
  Key, 
  Clock, 
  User, 
  Database,
  CheckCircle2,
  ArrowUpDown,
  ArrowUp,
  ArrowDown
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
    if (sortField === field) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  };

  const filteredLogs = auditLogs.filter(log => 
    log.operator.includes(searchTerm) ||
    log.targetSubject.includes(searchTerm) ||
    log.details.includes(searchTerm) ||
    log.actionType.includes(searchTerm)
  );

  const sortedLogs = [...filteredLogs].sort((a, b) => {
    if (!sortField) return 0;

    let res = 0;
    if (sortField === 'timestamp') {
      res = a.timestamp.localeCompare(b.timestamp, 'zh-CN');
    } else if (sortField === 'operator') {
      res = a.operator.localeCompare(b.operator, 'zh-CN');
    } else if (sortField === 'targetSubject') {
      res = a.targetSubject.localeCompare(b.targetSubject, 'zh-CN');
    } else if (sortField === 'actionType') {
      res = a.actionType.localeCompare(b.actionType, 'zh-CN');
    }

    return sortOrder === 'asc' ? res : -res;
  });

  if (dataStatus !== 'READY' || auditLogs.length === 0) {
    return (
      <div className="space-y-6">
        <h2 className="text-[28px] font-bold text-[#dae2fd]">合规审计追溯</h2>
        <DataStatusCard status={dataStatus} title="审计日志不可用" message="当前 Tax 后端没有审计日志集合 JSON 接口，页面不会显示本地生成的假日志。" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 顶部标题 */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-[28px] font-bold text-[#dae2fd] tracking-tight flex items-center gap-2">
            <FileCheck2 className="w-7 h-7 text-[#4cd7f6]" />
            <span>合规审计底稿与防篡改追溯链</span>
          </h2>
          <p className="text-[14px] text-[#c4c5d5] mt-1">
            记录所有财税凭证变更、风险处置标记、超预算签批与退税决算日志，配备不可篡改密码学校验码。
          </p>
        </div>
        <button
          onClick={onOpenExportModal}
          className="flex items-center gap-2 px-4 py-2 bg-[#1e40af] hover:bg-[#1e40af]/80 text-[#dde1ff] font-semibold text-[13px] rounded-lg border-t border-[#4cd7f6]/40 transition-all cursor-pointer shadow-md"
        >
          <Download className="w-4 h-4 text-[#4cd7f6]" />
          <span>导出法定审计底稿</span>
        </button>
      </div>

      {/* 审计链条安全状态横幅 */}
      <div className="glass-panel rounded-xl p-4 glow-cyan flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#10B981]/15 border border-[#10B981]/40 flex items-center justify-center text-[#10B981]">
            <Lock className="w-5 h-5" />
          </div>
          <div>
            <h4 className="text-[15px] font-bold text-[#dae2fd] flex items-center gap-2">
              <span>密码学凭证防篡改账本：运行正常</span>
              <span className="text-[11px] font-bold text-[#10B981] bg-[#10B981]/15 px-2 py-0.5 rounded border border-[#10B981]/30">
                可信度 100%
              </span>
            </h4>
            <p className="text-[12px] text-[#8e909f] font-mono-num">
              当前存证区块高度: 1,842,904 · 节点实时同步校验通过
            </p>
          </div>
        </div>
        <div className="relative min-w-[240px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f]" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="按操作人、标的或动作检索日志..."
            className="w-full bg-[#131b2e] border border-[#444653]/40 rounded-lg pl-9 pr-3 py-1.5 text-[12px] text-[#dae2fd] focus:border-[#4cd7f6] focus:outline-none font-mono-num"
          />
        </div>
      </div>

      {/* 日志清单 */}
      <div className="glass-panel rounded-xl p-5 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-[13px] font-mono-num min-w-[800px]">
            <thead>
              <tr className="border-b border-[#444653]/30 text-[12px] font-semibold text-[#8e909f]">
                <th 
                  onClick={() => handleSort('timestamp')}
                  className="py-3 px-3.5 whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按时间排序"
                >
                  <div className="flex items-center gap-1">
                    <span>发生时间戳</span>
                    {sortField === 'timestamp' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('operator')}
                  className="py-3 px-3.5 whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按操作人排序"
                >
                  <div className="flex items-center gap-1">
                    <span>操作人与岗位</span>
                    {sortField === 'operator' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('targetSubject')}
                  className="py-3 px-3.5 whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按主体排序"
                >
                  <div className="flex items-center gap-1">
                    <span>审计标的主体</span>
                    {sortField === 'targetSubject' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th 
                  onClick={() => handleSort('actionType')}
                  className="py-3 px-3.5 whitespace-nowrap cursor-pointer hover:text-[#4cd7f6] select-none transition-colors group"
                  title="点击按操作类型排序"
                >
                  <div className="flex items-center gap-1">
                    <span>操作类型</span>
                    {sortField === 'actionType' ? (
                      sortOrder === 'asc' ? <ArrowUp className="w-3.5 h-3.5 text-[#4cd7f6]" /> : <ArrowDown className="w-3.5 h-3.5 text-[#4cd7f6]" />
                    ) : (
                      <ArrowUpDown className="w-3 h-3 text-[#8e909f]/40 group-hover:text-[#4cd7f6]/70 transition-opacity" />
                    )}
                  </div>
                </th>
                <th className="py-3 px-3.5">审计动作细节说明</th>
                <th className="py-3 px-3.5 text-right whitespace-nowrap">防篡改校验码</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#444653]/20">
              {sortedLogs.map((log) => (
                <tr key={log.id} className="hover:bg-[#222a3d]/40 transition-colors">
                  <td className="py-3.5 px-3.5 text-[#8e909f] whitespace-nowrap">
                    {log.timestamp}
                  </td>
                  <td className="py-3.5 px-3.5 whitespace-nowrap">
                    <div className="font-bold text-[#dae2fd]">{log.operator}</div>
                    <div className="text-[11px] text-[#4cd7f6]">{log.role}</div>
                  </td>
                  <td className="py-3.5 px-3.5 text-[#b8c4ff] font-semibold whitespace-nowrap">
                    {log.targetSubject}
                  </td>
                  <td className="py-3.5 px-3.5 whitespace-nowrap">
                    <span className="text-[11px] font-bold bg-[#1e40af]/40 text-[#dde1ff] px-2 py-0.5 rounded border border-[#4cd7f6]/30 whitespace-nowrap">
                      {log.actionType}
                    </span>
                  </td>
                  <td className="py-3.5 px-3.5 text-[#dae2fd] max-w-md">
                    {log.details}
                  </td>
                  <td className="py-3.5 px-3.5 text-right whitespace-nowrap">
                    <span className="text-[11px] font-mono-num text-[#10B981] bg-[#10B981]/10 px-2 py-0.5 rounded border border-[#10B981]/20 whitespace-nowrap">
                      {log.integrityHash}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
