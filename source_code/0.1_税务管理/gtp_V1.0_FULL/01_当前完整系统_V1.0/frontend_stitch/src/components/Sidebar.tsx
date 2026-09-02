import {
  AlertTriangle,
  BrainCircuit,
  Building2,
  ChevronRight,
  Compass,
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
    {
      label: '集团',
      items: [
        { id: 'dashboard', label: '集团经营总览', icon: LayoutDashboard },
      ],
    },
    {
      label: '法人主体',
      items: [
        { id: 'entity-profile', label: '法人经营画像', icon: Landmark, badge: '待接入', disabled: true },
        { id: 'tax-ledger', label: '法人法定税务', icon: ReceiptText },
      ],
    },
    {
      label: '项目工程',
      items: [
        { id: 'projects', label: '项目工程库', icon: Building2 },
      ],
    },
    {
      label: '智能决策',
      items: [
        { id: 'tax-planning', label: 'AI 税务筹划', icon: Compass },
        { id: 'ai-review', label: 'AI 审查与审单', icon: BrainCircuit },
      ],
    },
    {
      label: '风险与治理',
      items: [
        { id: 'risk-center', label: '风控预警中心', icon: AlertTriangle, badge: unresolvedRiskCount },
        { id: 'audit', label: '合规审计追溯', icon: FileCheck2 },
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

  return (
    <nav className={containerClasses} aria-label="业务域导航">
      <div className="px-3.5 py-4 border-b border-[#444653]/30 flex items-center justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#1e40af] to-[#03b5d3]/40 flex-shrink-0 flex items-center justify-center border border-[#4cd7f6]/40 shadow-[0_0_10px_rgba(76,215,246,0.3)]">
            <ShieldCheck className="w-4 h-4 text-[#4cd7f6]" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-[15px] font-bold tracking-tight text-[#dde1ff] truncate">锐宝财税智控</h1>
            <p className="text-[10px] font-medium text-[#4cd7f6] tracking-wider truncate">风控管控中枢</p>
          </div>
        </div>
        {isMobile && onCloseMobile && (
          <button
            type="button"
            onClick={onCloseMobile}
            className="p-1.5 rounded-lg text-[#8e909f] hover:text-[#dae2fd] hover:bg-[#222a3d] cursor-pointer"
            title="关闭菜单"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      <div className="flex-1 flex flex-col px-2 py-3 overflow-y-auto scrollbar-hide">
        {navGroups.map((group, groupIndex) => (
          <section key={group.label} className={groupIndex === 0 ? '' : 'mt-3'} aria-labelledby={`nav-group-${groupIndex}`}>
            <h2 id={`nav-group-${groupIndex}`} className="px-2 pb-1.5 text-[10px] font-semibold text-[#8e909f] tracking-wider uppercase">
              {group.label}
            </h2>
            <div className="flex flex-col gap-1">
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = !item.disabled && currentTab === item.id;
                const isTaxPlanning = item.id === 'tax-planning';
                const showBadge = typeof item.badge === 'string' || (typeof item.badge === 'number' && item.badge > 0);

                return (
                  <button
                    type="button"
                    key={item.id}
                    disabled={item.disabled}
                    aria-current={isActive ? 'page' : undefined}
                    onClick={() => {
                      if (!item.disabled) onSelectTab(item.id);
                    }}
                    className={`flex items-center justify-between w-full px-2.5 py-2.5 rounded-lg font-medium transition-all duration-150 group ${
                      isTaxPlanning ? 'text-[15.5px] font-bold tracking-wide' : 'text-[13px]'
                    } ${
                      item.disabled
                        ? 'text-[#6f7280] bg-[#131b2e]/40 cursor-not-allowed opacity-75'
                        : isActive
                          ? 'text-[#4cd7f6] font-semibold bg-[#03b5d3]/15 border-l-2 border-[#4cd7f6] shadow-[0_0_10px_rgba(76,215,246,0.15)] cursor-pointer'
                          : isTaxPlanning
                            ? 'text-[#c4b5fd] hover:text-[#dde1ff] hover:bg-[#8b5cf6]/20 bg-[#8b5cf6]/10 border border-[#8b5cf6]/30 shadow-[0_0_8px_rgba(139,92,246,0.2)] cursor-pointer'
                            : 'text-[#c4c5d5] hover:text-[#dae2fd] hover:bg-[#222a3d]/60 cursor-pointer'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <Icon className={`flex-shrink-0 transition-transform ${item.disabled ? '' : 'group-hover:scale-110'} ${
                        isTaxPlanning ? 'w-5 h-5' : 'w-4 h-4'
                      } ${isActive ? 'text-[#4cd7f6]' : isTaxPlanning ? 'text-[#a78bfa]' : 'text-[#8e909f]'}`} />
                      <span className="truncate">{item.label}</span>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {showBadge && (
                        <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded-full ${
                          item.disabled
                            ? 'bg-[#334155] text-[#cbd5e1]'
                            : 'bg-[#EF4444] text-white shadow-[0_0_8px_rgba(239,68,68,0.6)]'
                        }`}>
                          {item.badge}
                        </span>
                      )}
                      {isActive && <ChevronRight className="w-3 h-3 text-[#4cd7f6]" />}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <div className="p-3 border-t border-[#444653]/30 bg-[#060e20]/40">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <div className="w-5 h-5 rounded-md bg-[#10B981]/15 border border-[#10B981]/30 flex items-center justify-center flex-shrink-0">
              <ShieldCheck className="w-3 h-3 text-[#10B981]" />
            </div>
            <p className="text-[11px] font-semibold flex items-center gap-1 truncate" style={{ color: statusStyles.text }} title={aiModelStatus.message}>
              <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: statusStyles.dot }} />
              {aiModelStatus.message}
            </p>
          </div>
          <a
            href="/ai-models"
            className="text-[10px] px-1.5 py-0.5 rounded bg-[#1e293b] text-[#93c5fd] hover:bg-[#334155] border border-[#3b82f6]/30"
            title="查看与配置 AI 模型端点"
          >
            模型管理
          </a>
        </div>
        <div className="flex items-center justify-between pt-2 border-t border-[#444653]/30">
          <a
            href="/logout"
            className="flex items-center gap-1.5 text-[11px] text-[#8e909f] hover:text-[#ef4444] transition-colors"
            title="退出当前登录状态"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span>退出登录</span>
          </a>
          <a
            href="/classic"
            className="text-[10px] text-[#4cd7f6]/70 hover:text-[#4cd7f6] hover:underline"
            title="切换到纯数据经典管理后台"
          >
            经典模式
          </a>
        </div>
      </div>
    </nav>
  );
}
