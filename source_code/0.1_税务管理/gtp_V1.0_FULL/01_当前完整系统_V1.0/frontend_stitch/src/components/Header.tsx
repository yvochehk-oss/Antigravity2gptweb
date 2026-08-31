import { useState, useEffect } from 'react';
import { 
  Search, 
  Bell, 
  Settings, 
  Sparkles, 
  Menu, 
  Radio,
  User as UserIcon,
} from 'lucide-react';
import { DataStatus, SystemSettings } from '../types';
import { SettingsModal } from './SettingsModal';
import { UserProfileModal } from './UserProfileModal';

interface HeaderProps {
  onToggleAi: () => void;
  isAiOpen: boolean;
  onOpenExportModal?: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  onToggleMobileMenu?: () => void;
  settings: SystemSettings;
  onSaveSettings: (newSettings: SystemSettings) => void;
  riskStatus: DataStatus;
  unresolvedRiskCount: number;
}

export function Header({
  onToggleAi,
  isAiOpen,
  searchQuery,
  onSearchChange,
  onToggleMobileMenu,
  settings,
  onSaveSettings,
  riskStatus,
  unresolvedRiskCount,
}: HeaderProps) {
  const [showNotificationList, setShowNotificationList] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [userProfile, setUserProfile] = useState<{ nickname?: string; avatar_url?: string; role?: string } | null>(null);

  const hasUnreadRisks = riskStatus === 'READY' && unresolvedRiskCount > 0;

  // 加载顶部头像与昵称
  useEffect(() => {
    fetch('/api/v1/user-center/profile')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data) setUserProfile(data);
      })
      .catch(() => {});
  }, []);

  return (
    <header className="fixed top-0 right-0 left-0 md:left-48 h-16 bg-[#0b1326]/80 backdrop-blur-md border-b border-[#444653]/30 z-30 flex items-center justify-between px-4 md:px-6 gap-4">
      {/* 左侧：移动端菜单 + 实时监管胶囊 + 全局检索栏 (向左移) */}
      <div className="flex items-center gap-3 sm:gap-4 flex-1 min-w-0">
        <button 
          onClick={onToggleMobileMenu}
          className="md:hidden p-2 rounded-lg text-[#dae2fd] hover:bg-[#222a3d] flex-shrink-0"
          title="打开导航菜单"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#03b5d3]/10 border border-[#4cd7f6]/20 text-[11px] text-[#4cd7f6] flex-shrink-0">
          <Radio className="w-3 h-3 text-[#4cd7f6] animate-pulse" />
          <span>实时监管中 · 四流核验{settings.autoFourFlowsMatch ? '已激活' : '抽查模式'}</span>
        </div>

        {/* 全局检索栏 (左移至此处) */}
        <div className="relative group flex-1 max-w-xs md:max-w-sm lg:max-w-md min-w-[180px]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#8e909f] group-focus-within:text-[#4cd7f6] transition-colors" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="搜索工程项目、税务凭证、发票号码..."
            className="w-full bg-[#131b2e] border-b border-[#444653]/50 focus:border-[#4cd7f6] text-[12px] font-mono-num text-[#dae2fd] pl-9 pr-3 py-1.5 rounded-t focus:outline-none focus:bg-[#171f33] transition-all placeholder:text-[#8e909f]"
          />
        </div>
      </div>

      {/* 右侧：快捷工具、用户头像与AI助手 */}
      <div className="flex items-center gap-1.5 sm:gap-3 flex-shrink-0">

        {/* AI助手唤醒按钮 */}
        <button
          onClick={onToggleAi}
          className={`flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg text-[12px] font-semibold border transition-all cursor-pointer ${
            isAiOpen
              ? 'bg-[#03b5d3]/20 border-[#4cd7f6] text-[#4cd7f6] shadow-[0_0_12px_rgba(76,215,246,0.3)]'
              : 'bg-[#171f33] border-[#444653]/50 text-[#dae2fd] hover:border-[#4cd7f6]/50 hover:text-[#4cd7f6]'
          }`}
          title="切换智能风控助手"
        >
          <Sparkles className="w-3.5 h-3.5 text-[#4cd7f6] animate-slow-pulse" />
          <span className="hidden sm:inline">锐宝智能助手</span>
          <span className="sm:hidden text-[11px]">AI助手</span>
        </button>

        {/* 预警通知 */}
        <div className="relative">
          <button
            onClick={() => setShowNotificationList(!showNotificationList)}
            className="p-2 rounded-full text-[#c4c5d5] hover:text-[#dae2fd] hover:bg-[#222a3d] transition-colors relative cursor-pointer"
            title="预警通知"
            aria-expanded={showNotificationList}
          >
            <Bell className="w-4 h-4" />
            {hasUnreadRisks && (
              <span
                className="absolute top-1 right-1 w-2 h-2 rounded-full bg-[#EF4444] shadow-[0_0_6px_#EF4444]"
                aria-label={`${unresolvedRiskCount} 条未闭环风险`}
              />
            )}
          </button>

          {showNotificationList && (
            <div className="absolute right-0 mt-2 w-[calc(100vw-2rem)] max-w-xs sm:w-80 bg-[#171f33] border border-[#4cd7f6]/30 rounded-xl shadow-2xl p-4 z-50">
              <div className="flex items-center justify-between pb-2 border-b border-[#444653]/30">
                <span className="text-[13px] font-bold text-[#dae2fd]">实时涉税与成本预警</span>
                <span className="text-[11px] text-[#4cd7f6] cursor-pointer hover:underline" onClick={() => setShowNotificationList(false)}>全部已读</span>
              </div>
              <div className="mt-2 space-y-2 text-[12px]" role="status" aria-live="polite">
                {riskStatus === 'READY' ? (
                  <div className="p-2.5 rounded-lg bg-[#10B981]/10 border border-[#10B981]/30">
                    <p className="font-semibold text-[#10B981]">
                      {unresolvedRiskCount > 0 ? `当前有 ${unresolvedRiskCount} 条未闭环风险` : '当前没有未闭环风险'}
                    </p>
                    <p className="text-[11px] text-[#c4c5d5] mt-1">
                      进入“风控预警中心”查看后端返回的风险详情与处置状态。
                    </p>
                  </div>
                ) : (
                  <div className="p-2.5 rounded-lg bg-[#F59E0B]/10 border border-[#F59E0B]/30">
                    <p className="font-semibold text-[#F59E0B]">风险集合 {riskStatus}</p>
                    <p className="text-[11px] text-[#c4c5d5] mt-1">
                      当前 Tax 后端未提供风险集合接口，页面未加载本地演示预警。
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* 系统设置 */}
        <button
          onClick={() => setShowSettingsModal(true)}
          className="p-2 rounded-full text-[#c4c5d5] hover:text-[#dae2fd] hover:bg-[#222a3d] transition-colors cursor-pointer relative group"
          title="系统运行参数与风控阈值配置"
        >
          <Settings className="w-4 h-4 group-hover:rotate-45 transition-transform" />
          <span className="sr-only">系统参数设置</span>
        </button>

        {/* 用户个人中心与头像入口 */}
        <button
          onClick={() => setShowProfileModal(true)}
          className="flex items-center gap-2 pl-2 pr-2.5 py-1 rounded-full bg-[#131b2e] border border-[#444653]/40 hover:border-[#a78bfa]/60 transition-all cursor-pointer group"
          title="个人中心与账号安全"
        >
          <img
            src={userProfile?.avatar_url || '/static/avatars/default.png'}
            alt="头像"
            onError={(e) => {
              (e.target as HTMLImageElement).src = 'https://api.dicebear.com/7.x/bottts/svg?seed=admin';
            }}
            className="w-6 h-6 rounded-full object-cover border border-[#a78bfa]/40 bg-[#0b1326]"
          />
          <span className="text-[12px] font-medium text-[#dae2fd] group-hover:text-[#dde1ff] hidden sm:inline max-w-[80px] truncate">
            {userProfile?.nickname || '管理员'}
          </span>
        </button>

        {/* 个人中心弹窗 */}
        <UserProfileModal
          isOpen={showProfileModal}
          onClose={() => setShowProfileModal(false)}
          onProfileUpdated={(updated) => setUserProfile(updated)}
        />

        {/* 交互式系统设置弹窗 */}
        <SettingsModal
          isOpen={showSettingsModal}
          onClose={() => setShowSettingsModal(false)}
          settings={settings}
          onSaveSettings={onSaveSettings}
        />
      </div>
    </header>
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
