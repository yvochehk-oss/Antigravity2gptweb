import {
  AlertTriangle,
  BrainCircuit,
  Building2,
  ChevronRight,
  FileCheck2,
  KeyRound,
  Landmark,
  LayoutDashboard,
  LogOut,
  ReceiptText,
  Server,
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
  onOpenTokenHubSetup?: () => void;
  onOpenRagSetup?: () => void;
}

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  badge?: number | string;
  disabled?: boolean;
  action?: 'tokenhub-setup' | 'rag-setup';
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

export function Sidebar({ currentTab, onSelectTab, unresolvedRiskCount, aiModelStatus, isMobile, onCloseMobile, onOpenTokenHubSetup, onOpenRagSetup }: SidebarProps) {
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
      label: 'AI 接入',
      items: [
        { id: 'tokenhub-setup', label: 'TokenHub API Key 配置', icon: KeyRound, action: 'tokenhub-setup' },
        { id: 'rag-setup', label: 'RAG 知识库连接配置', icon: Server, action: 'rag-setup' },
      ],
    },
    {
      label: '风险与治理',
      items: [
        { id: 'risk-center', label: '风控中心', icon: AlertTriangle, badge: unresolvedRiskCount },
        { id: 'audit', label: '合规审计', icon: FileCheck2 },
      ],
    },
  ];

  const containerClasses = isMobile
    ? 'flex h-full w-full flex-col bg-[var(--color-surface)] text-[var(--color-text-primary)]'
    : 'fixed left-0 top-0 z-40 hidden h-screen w-[var(--sidebar-width)] flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] md:flex';
  const statusClass = {
    LOADING: 'text-[var(--color-warning)]',
    READY: 'text-[var(--color-success)]',
    DEGRADED: 'text-[var(--color-warning)]',
    UNAVAILABLE: 'text-[var(--color-danger)]',
  }[aiModelStatus.state];
  const statusDotClass = {
    LOADING: 'bg-[var(--color-warning)]',
    READY: 'bg-[var(--color-success)]',
    DEGRADED: 'bg-[var(--color-warning)]',
    UNAVAILABLE: 'bg-[var(--color-danger)]',
  }[aiModelStatus.state];
  const modelStatusText = `模型服务：${dataStatusLabel(aiModelStatus.state)}`;
  const modelStatusTitle = `${modelStatusText}（${aiModelStatus.message}）`;

  return (
    <nav className={containerClasses} aria-label="业务域导航">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3.5 py-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[var(--radius-base)] border border-[var(--color-brand)]/35 bg-[var(--color-brand-muted)]">
            <ShieldCheck className="h-4 w-4 text-[var(--color-brand)]" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[13.5px] font-bold tracking-tight text-[var(--color-text-primary)]">锐宝财税智控</h1>
            <p className="truncate text-[10px] font-semibold text-[var(--color-text-secondary)]">风控管控中枢</p>
          </div>
        </div>
        {isMobile && onCloseMobile && (
          <button
            type="button"
            onClick={onCloseMobile}
            className="cursor-pointer rounded-[var(--radius-sm)] p-1.5 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text-primary)]"
            title="关闭菜单"
          >
            <X className="h-5 w-5" />
          </button>
        )}
      </div>

      <div className="scrollbar-hide flex flex-1 flex-col overflow-y-auto px-2 py-3">
        {navGroups.map((group, groupIndex) => (
          <section key={group.label} className={groupIndex === 0 ? '' : 'mt-3'} aria-labelledby={`nav-group-${groupIndex}`}>
            <h2 id={`nav-group-${groupIndex}`} className="px-2 pb-1.5 text-[10px] font-semibold tracking-wide text-[var(--color-text-muted)]">
              {group.label}
            </h2>
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
                    onClick={() => {
                      if (item.disabled) return;
                      if (item.action === 'tokenhub-setup' && onOpenTokenHubSetup) {
                        onOpenTokenHubSetup();
                        return;
                      }
                      if (item.action === 'rag-setup' && onOpenRagSetup) {
                        onOpenRagSetup();
                        return;
                      }
                      onSelectTab(item.id);
                    }}
                    className={`group flex w-full items-center justify-between rounded-[var(--radius-base)] border px-2.5 py-2.5 font-medium transition-colors duration-150 ${
                      isIntelligentDecision ? 'ai-decision-nav text-[15px] font-semibold' : 'text-[13px]'
                    } ${
                      item.disabled
                        ? 'cursor-not-allowed border-transparent bg-[var(--color-surface)]/45 text-[var(--color-text-muted)] opacity-75'
                        : isActive
                          ? 'cursor-pointer border-[var(--color-brand)]/35 bg-[var(--color-brand-muted)] font-semibold text-[var(--color-brand)]'
                          : isIntelligentDecision
                            ? 'cursor-pointer border-[var(--color-brand)]/45 bg-[var(--color-surface)] text-[var(--color-text-primary)] hover:border-[var(--color-brand)]/75 hover:bg-[var(--color-brand-muted)]'
                            : 'cursor-pointer border-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text-primary)]'
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-2.5">
                      <Icon
                        className={`${isIntelligentDecision ? 'h-[18px] w-[18px]' : 'h-4 w-4'} flex-shrink-0 transition-colors ${
                          isActive
                            ? 'text-[var(--color-brand)]'
                            : isIntelligentDecision
                              ? 'text-[var(--color-brand)]'
                              : 'text-[var(--color-text-muted)] group-hover:text-[var(--color-text-secondary)]'
                        }`}
                      />
                      <span className={`truncate ${isIntelligentDecision ? 'font-semibold tracking-[0.01em]' : ''}`}>{item.label}</span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-1">
                      {showBadge && (
                        <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${item.disabled ? 'bg-[var(--color-surface-2)] text-[var(--color-text-secondary)]' : 'bg-[var(--color-danger)] text-white'}`}>
                          {item.badge}
                        </span>
                      )}
                      {isActive && <ChevronRight className="h-3 w-3 text-[var(--color-brand)]" />}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-2)]/45 p-3">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <div className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-success)]/30 bg-[var(--color-success)]/10">
              <ShieldCheck className="h-3 w-3 text-[var(--color-success)]" />
            </div>
            <p className={`flex min-w-0 items-center gap-1 truncate text-[11px] font-semibold ${statusClass}`} title={modelStatusTitle}>
              <span className={`inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full ${statusDotClass}`} />
              {modelStatusText}
            </p>
          </div>
          <a
            href="/ai-models"
            className="flex-shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-border)] bg-[var(--color-surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-brand)]/35 hover:text-[var(--color-brand)]"
            title="查看与配置智能模型端点"
          >
            模型管理
          </a>
        </div>
        <div className="flex items-center justify-between border-t border-[var(--color-border)] pt-2">
          <a href="/logout" className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]" title="退出当前登录状态">
            <LogOut className="h-3.5 w-3.5" />
            <span>退出登录</span>
          </a>
          <a href="/classic" className="text-[10px] text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-brand)] hover:underline" title="切换到纯数据经典管理后台">
            经典模式
          </a>
        </div>
      </div>
    </nav>
  );
}
