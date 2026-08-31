import { 
  LayoutDashboard, 
  Building2, 
  ReceiptText, 
  AlertTriangle, 
  BrainCircuit, 
  FileCheck2, 
  ShieldCheck,
  Compass,
  ChevronRight,
  LogOut,
  X
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

export function Sidebar({ currentTab, onSelectTab, unresolvedRiskCount, aiModelStatus, isMobile, onCloseMobile }: SidebarProps) {
  const menuItems = [
    { id: 'dashboard', label: '全局仪表盘', icon: LayoutDashboard },
    { id: 'projects', label: '项目工程库', icon: Building2 },
    { id: 'tax-ledger', label: '税务台账', icon: ReceiptText },
    { id: 'tax-planning', label: 'AI税务筹划', icon: Compass },
    { id: 'risk-center', label: '风控预警中心', icon: AlertTriangle, badge: unresolvedRiskCount },
    { id: 'ai-review', label: 'AI 审查与审单', icon: BrainCircuit },
    { id: 'audit', label: '合规审计追溯', icon: FileCheck2 },
  ];

  const containerClasses = isMobile
    ? "flex flex-col h-full w-full bg-[#0b1326] text-[#dde1ff]"
    : "hidden md:flex flex-col h-screen w-48 fixed left-0 top-0 bg-[#0b1326]/95 backdrop-blur-xl border-r border-[#444653]/30 z-40";
  const statusStyles = {
    LOADING: { text: '#F59E0B', dot: '#F59E0B' },
    READY: { text: '#10B981', dot: '#10B981' },
    DEGRADED: { text: '#F59E0B', dot: '#F59E0B' },
    UNAVAILABLE: { text: '#F87171', dot: '#F87171' },
  }[aiModelStatus.state];

  return (
    <nav className={containerClasses}>
      {/* 头部品牌 */}
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
            onClick={onCloseMobile}
            className="p-1.5 rounded-lg text-[#8e909f] hover:text-[#dae2fd] hover:bg-[#222a3d] cursor-pointer"
            title="关闭菜单"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* 导航项 */}
      <div className="flex-1 flex flex-col gap-1 px-2 py-3 overflow-y-auto scrollbar-hide">
        <div className="px-2 pb-1.5 text-[10px] font-semibold text-[#8e909f] tracking-wider uppercase">业务管控模块</div>
        {menuItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;
          const isTaxPlanning = item.id === 'tax-planning';
          
          return (
            <button
              key={item.id}
              onClick={() => onSelectTab(item.id)}
              className={`flex items-center justify-between w-full px-2.5 py-2.5 rounded-lg font-medium transition-all duration-150 group cursor-pointer ${
                isTaxPlanning ? 'text-[15.5px] font-bold tracking-wide' : 'text-[13px]'
              } ${
                isActive
                  ? 'text-[#4cd7f6] font-semibold bg-[#03b5d3]/15 border-l-2 border-[#4cd7f6] shadow-[0_0_10px_rgba(76,215,246,0.15)]'
                  : isTaxPlanning
                  ? 'text-[#c4b5fd] hover:text-[#dde1ff] hover:bg-[#8b5cf6]/20 bg-[#8b5cf6]/10 border border-[#8b5cf6]/30 shadow-[0_0_8px_rgba(139,92,246,0.2)] animate-pulse'
                  : 'text-[#c4c5d5] hover:text-[#dae2fd] hover:bg-[#222a3d]/60'
              }`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <Icon className={`flex-shrink-0 transition-transform group-hover:scale-110 ${
                  isTaxPlanning ? 'w-5 h-5' : 'w-4 h-4'
                } ${isActive ? 'text-[#4cd7f6]' : isTaxPlanning ? 'text-[#a78bfa]' : 'text-[#8e909f]'}`} />
                <span className="truncate">{item.label}</span>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                {item.badge && item.badge > 0 && (
                  <span className="px-1.5 py-0.2 text-[9px] font-bold rounded-full bg-[#EF4444] text-white shadow-[0_0_8px_rgba(239,68,68,0.6)] animate-pulse">
                    {item.badge}
                  </span>
                )}
                {isActive && <ChevronRight className="w-3 h-3 text-[#4cd7f6]" />}
              </div>
            </button>
          );
        })}
      </div>

      {/* 底部系统状态与退出 */}
      <div className="p-3 border-t border-[#444653]/30 bg-[#060e20]/40">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <div className="w-5 h-5 rounded-md bg-[#10B981]/15 border border-[#10B981]/30 flex items-center justify-center flex-shrink-0">
              <ShieldCheck className="w-3 h-3 text-[#10B981]" />
            </div>
            <p className="text-[11px] font-semibold flex items-center gap-1 truncate" style={{ color: statusStyles.text }} title={aiModelStatus.message}>
              <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: statusStyles.dot }}></span>
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

/* ===========================================================
   保持 Windows ClearType 亚像素渲染（系统字体方案下无需 swap）
   -webkit-font-smoothing: auto  -> Windows 使用 ClearType
                                -> macOS  使用视网膜灰度平滑
   =========================================================== */
*, *::before, *::after, html, body, .antialiased {
  -webkit-font-smoothing: auto !important;
  -moz-osx-font-smoothing: auto !important;
  text-rendering: auto !important;
}
