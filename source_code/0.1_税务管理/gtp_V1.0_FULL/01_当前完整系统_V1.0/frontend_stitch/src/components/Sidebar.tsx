import {
  AlertTriangle,
  BrainCircuit,
  Building2,
  ChevronRight,
  FileCheck2,
  Landmark,
  LayoutDashboard,
  LogOut,
  ReceiptText,
  ShieldCheck,
  X,
  type LucideIcon,
} from 'lucide-react';
import { AiModelStatus } from '../types';
import { dataStatusLabel } from './uiLocalization';

interface SidebarProps {
  currentTab: string;
  onSelectTab: (tab: string) => void;
  unresolvedRiskCount: number;
  aiModelStatus: AiModelStatus;
  isMobile?: boolean;
  onCloseMobile?: () => void;
}

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  badge?: number | string;
  disabled?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

export function Sidebar({ currentTab, onSelectTab, unresolvedRiskCount, aiModelStatus, isMobile, onCloseMobile }: SidebarProps) {
  const navGroups: NavGroup[] = [
    { label: '集团', items: [{ id: 'dashboard', label: '集团经营总览', icon: LayoutDashboard }] },
    {
      label: '法人主体',
      items: [
        { id: 'entity-profile', label: '法人经营画像', icon: Landmark },
        { id: 'tax-ledger', label: '法人法定税务', icon: ReceiptText },
      ],
    },
    { label: '项目工程', items: [{ id: 'projects', label: '项目工程库', icon: Building2 }] },
    { label: '智能决策', items: [{ id: 'ai-decision', label: '智能财税决策中心', icon: BrainCircuit }] },
    {
      label: '风险与治理',
      items: [
        { id: 'risk-center', label: '风控中心', icon: AlertTriangle, badge: unresolvedRiskCount },
        { id: 'audit', label: '合规审计', icon: FileCheck2 },
      ],
    },
  ];

  const containerClasses = isMobile
    ? 'flex flex-col h-full w-full bg-[#0b1326] text-[#dde1ff]'
    : 'hidden md:flex flex-col h-screen w-48 fixed left-0 top-0 bg-[#0b1326]/95 backdrop-blur-xl border-r border-[#444653]/30 z-40';
  const statusStyles = {
    LOADING: { text: '#F59E0B', dot: '#F59E0B' },
    READY: { text: '#10B981', dot: '#10B981' },
    DEGRADED: { text: '#F59E0B', dot: '#F59E0B' },
    UNAVAILABLE: { text: '#F87171', dot: '#F87171' },
  }[aiModelStatus.state];
  const modelStatusText = `模型服务：${dataStatusLabel(aiModelStatus.state)}`;

  return (
    <nav className={containerClasses} aria-label="业务域导航">
      <div className="flex items-center justify-between border-b border-[#444653]/30 px-3.5 py-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-[#4cd7f6]/40 bg-gradient-to-br from-[#1e40af] to-[#03b5d3]/40 shadow-[0_0_10px_rgba(76,215,246,0.3)]"><ShieldCheck className="h-4 w-4 text-[#4cd7f6]" /></div>
          <div className="min-w-0 flex-1"><h1 className="truncate text-[15px] font-bold tracking-tight text-[#dde1ff]">锐宝财税智控</h1><p className="truncate text-[10px] font-medium tracking-wider text-[#4cd7f6]">风控管控中枢</p></div>
        </div>
        {isMobile && onCloseMobile && <button type="button" onClick={onCloseMobile} className="cursor-pointer rounded-lg p-1.5 text-[#8e909f] hover:bg-[#222a3d] hover:text-[#dae2fd]" title="关闭菜单"><X className="h-5 w-5" /></button>}
      </div>

      <div className="scrollbar-hide flex flex-1 flex-col overflow-y-auto px-2 py-3">
        {navGroups.map((group, groupIndex) => (
          <section key={group.label} className={groupIndex === 0 ? '' : 'mt-3'} aria-labelledby={`nav-group-${groupIndex}`}>
            <h2 id={`nav-group-${groupIndex}`} className="px-2 pb-1.5 text-[10px] font-semibold tracking-wider text-[#8e909f]">{group.label}</h2>
            <div className="flex flex-col gap-1">
              {group.items.map(item => {
                const Icon = item.icon;
                const isActive = !item.disabled && currentTab === item.id;
                const isIntelligentDecision = item.id === 'ai-decision';
                const showBadge = typeof item.badge === 'string' || (typeof item.badge === 'number' && item.badge > 0);
                return (
                  <button
                    type="button"
                    key={item.id}
                    disabled={item.disabled}
                    aria-current={isActive ? 'page' : undefined}
                    onClick={() => { if (!item.disabled) onSelectTab(item.id); }}
                    className={`group flex w-full items-center justify-between rounded-lg px-2.5 py-2.5 font-medium transition-all duration-150 ${isIntelligentDecision ? 'text-[15px] font-bold tracking-wide' : 'text-[13px]'} ${item.disabled ? 'cursor-not-allowed bg-[#131b2e]/40 text-[#6f7280] opacity-75' : isActive ? 'cursor-pointer border-l-2 border-[#4cd7f6] bg-[#03b5d3]/15 font-semibold text-[#4cd7f6] shadow-[0_0_10px_rgba(76,215,246,0.15)]' : isIntelligentDecision ? 'cursor-pointer border border-[#8b5cf6]/30 bg-[#8b5cf6]/10 text-[#c4b5fd] shadow-[0_0_8px_rgba(139,92,246,0.2)] hover:bg-[#8b5cf6]/20 hover:text-[#dde1ff]' : 'cursor-pointer text-[#c4c5d5] hover:bg-[#222a3d]/60 hover:text-[#dae2fd]'}`}
                  >
                    <div className="flex min-w-0 items-center gap-2.5"><Icon className={`flex-shrink-0 transition-transform ${item.disabled ? '' : 'group-hover:scale-110'} ${isIntelligentDecision ? 'h-5 w-5' : 'h-4 w-4'} ${isActive ? 'text-[#4cd7f6]' : isIntelligentDecision ? 'text-[#a78bfa]' : 'text-[#8e909f]'}`} /><span className="truncate">{item.label}</span></div>
                    <div className="flex flex-shrink-0 items-center gap-1">{showBadge && <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${item.disabled ? 'bg-[#334155] text-[#cbd5e1]' : 'bg-[#EF4444] text-white shadow-[0_0_8px_rgba(239,68,68,0.6)]'}`}>{item.badge}</span>}{isActive && <ChevronRight className="h-3 w-3 text-[#4cd7f6]" />}</div>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <div className="border-t border-[#444653]/30 bg-[#060e20]/40 p-3">
        <div className="mb-1.5 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <div className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border border-[#10B981]/30 bg-[#10B981]/15"><ShieldCheck className="h-3 w-3 text-[#10B981]" /></div>
            <p className="flex items-center gap-1 truncate text-[11px] font-semibold" style={{ color: statusStyles.text }} title={modelStatusText}><span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusStyles.dot }} />{modelStatusText}</p>
          </div>
          <a href="/ai-models" className="rounded border border-[#3b82f6]/30 bg-[#1e293b] px-1.5 py-0.5 text-[10px] text-[#93c5fd] hover:bg-[#334155]" title="查看与配置智能模型端点">模型管理</a>
        </div>
        <div className="flex items-center justify-between border-t border-[#444653]/30 pt-2">
          <a href="/logout" className="flex items-center gap-1.5 text-[11px] text-[#8e909f] transition-colors hover:text-[#ef4444]" title="退出当前登录状态"><LogOut className="h-3.5 w-3.5" /><span>退出登录</span></a>
          <a href="/classic" className="text-[10px] text-[#4cd7f6]/70 hover:text-[#4cd7f6] hover:underline" title="切换到纯数据经典管理后台">经典模式</a>
        </div>
      </div>
    </nav>
  );
}
