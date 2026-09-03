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
    <header className="fixed top-0 right-0 left-0 md:left-[var(--sidebar-width)] z-30 flex h-14 items-center justify-between gap-4 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-4 md:px-6">
      {/* 左侧：移动端菜单 + 实时监管胶囊 + 全局检索栏 */}
      <div className="flex min-w-0 flex-1 items-center gap-3 sm:gap-4">
        <button
          onClick={onToggleMobileMenu}
          className="flex-shrink-0 rounded-lg p-2 text-[var(--color-text-primary)] transition-colors hover:bg-[var(--color-surface-2)] md:hidden"
          title="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </button>

        <div className="hidden flex-shrink-0 items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-[var(--font-xs)] text-[var(--color-text-secondary)] sm:flex">
          <Radio className="h-3 w-3 text-[var(--color-brand)] animate-slow-pulse" />
          <span>实时监管中 · 四流核验{settings.autoFourFlowsMatch ? '已激活' : '抽查模式'}</span>
        </div>

        <div className="group relative min-w-[180px] max-w-xs flex-1 md:max-w-sm lg:max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)] transition-colors group-focus-within:text-[var(--color-brand)]" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="搜索工程项目、税务凭证、发票号码..."
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-9 pr-3 text-[var(--font-xs)] font-mono-num text-[var(--color-text-primary)] transition-colors placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-brand)] focus:bg-[var(--color-surface-2)] focus:outline-none"
          />
        </div>
      </div>

      {/* 右侧：快捷工具、用户头像与智能助手 */}
      <div className="flex flex-shrink-0 items-center gap-1.5 sm:gap-3">
        <button
          onClick={onToggleAi}
          className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[var(--font-xs)] font-semibold transition-colors sm:px-3 ${
            isAiOpen
              ? 'border-[var(--color-brand)] bg-[var(--color-brand-muted)] text-[var(--color-brand)]'
              : 'border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-primary)] hover:border-[var(--color-brand)] hover:text-[var(--color-brand)]'
          }`}
          title="切换智能风控助手"
        >
          <Sparkles className="h-3.5 w-3.5 text-[var(--color-brand)] animate-slow-pulse" />
          <span className="hidden sm:inline">锐宝智能助手</span>
          <span className="sm:hidden text-[var(--font-xs)]">智能助手</span>
        </button>

        <div className="relative">
          <button
            onClick={() => setShowNotificationList(!showNotificationList)}
            className="relative cursor-pointer rounded-full p-2 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text-primary)]"
            title="预警通知"
            aria-expanded={showNotificationList}
          >
            <Bell className="h-4 w-4" />
            {hasUnreadRisks && (
              <span
                className="absolute right-1 top-1 h-2 w-2 rounded-full bg-[var(--color-danger)]"
                aria-label={`${unresolvedRiskCount} 条未闭环风险`}
              />
            )}
          </button>

          {showNotificationList && (
            <div className="absolute right-0 z-50 mt-2 w-[calc(100vw-2rem)] max-w-xs rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-[var(--shadow-elevated)] sm:w-80">
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-2">
                <span className="text-[var(--font-sm)] font-bold text-[var(--color-text-primary)]">实时涉税与成本预警</span>
                <span className="cursor-pointer text-[var(--font-xs)] text-[var(--color-brand)] hover:underline" onClick={() => setShowNotificationList(false)}>全部已读</span>
              </div>
              <div className="mt-2 space-y-2 text-[var(--font-xs)]" role="status" aria-live="polite">
                {riskStatus === 'READY' ? (
                  <div className="rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/10 p-2.5">
                    <p className="font-semibold text-[var(--color-success)]">
                      {unresolvedRiskCount > 0 ? `当前有 ${unresolvedRiskCount} 条未闭环风险` : '当前没有未闭环风险'}
                    </p>
                    <p className="mt-1 text-[var(--font-xs)] text-[var(--color-text-secondary)]">
                      进入“风控预警中心”查看后端返回的风险详情与处置状态。
                    </p>
                  </div>
                ) : (
                  <div className="rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 p-2.5">
                    <p className="font-semibold text-[var(--color-warning)]">风险集合 {riskStatus}</p>
                    <p className="mt-1 text-[var(--font-xs)] text-[var(--color-text-secondary)]">
                      当前 Tax 后端未提供风险集合接口，页面未加载本地演示预警。
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <button
          onClick={() => setShowSettingsModal(true)}
          className="group relative cursor-pointer rounded-full p-2 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text-primary)]"
          title="系统运行参数与风控阈值配置"
        >
          <Settings className="h-4 w-4 transition-transform group-hover:rotate-45" />
          <span className="sr-only">系统参数设置</span>
        </button>

        <button
          onClick={() => setShowProfileModal(true)}
          className="group flex cursor-pointer items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] py-1 pl-2 pr-2.5 transition-colors hover:border-[var(--color-brand)] hover:bg-[var(--color-surface-2)]"
          title="个人中心与账号安全"
        >
          <img
            src={userProfile?.avatar_url || '/static/avatars/default.png'}
            alt="头像"
            onError={(e) => {
              (e.target as HTMLImageElement).src = 'https://api.dicebear.com/7.x/bottts/svg?seed=admin';
            }}
            className="h-6 w-6 rounded-full border border-[var(--color-border)] bg-[var(--color-bg)] object-cover"
          />
          <span className="hidden max-w-[80px] truncate text-[var(--font-xs)] font-medium text-[var(--color-text-primary)] sm:inline">
            {userProfile?.nickname || '管理员'}
          </span>
        </button>

        <UserProfileModal
          isOpen={showProfileModal}
          onClose={() => setShowProfileModal(false)}
          onProfileUpdated={(updated) => setUserProfile(updated)}
        />

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
