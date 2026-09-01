import { useCallback, useEffect, useRef, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { DashboardView } from './components/DashboardView';
import { ProjectRepositoryView } from './components/ProjectRepositoryView';
import { ProjectDetailView } from './components/ProjectDetailView';
import { TaxLedgerView } from './components/TaxLedgerView';
import { TaxPlanningView } from './components/TaxPlanningView';
import { RiskCenterView } from './components/RiskCenterView';
import { AiReviewView } from './components/AiReviewView';
import { AuditView } from './components/AuditView';
import { AiAssistantDrawer } from './components/AiAssistantDrawer';
import { NewTaxRecordModal } from './components/NewTaxRecordModal';
import { ExportReportModal } from './components/ExportReportModal';
import { DataStatusCard } from './components/DataStatusCard';
import { DEFAULT_SETTINGS } from './components/SettingsModal';
import { askProjectAi, ApiError, fetchAiModelStatus, fetchAuditLogs, fetchConfiguredProjects, fetchRiskEvents, fetchTaxLedger, rebuildTaxLedger } from './api';
import {
  AssistantMessage,
  AiModelStatus,
  AuditTrailRecord,
  DataStatus,
  ProjectItem,
  RiskEvent,
  SystemSettings,
} from './types';

function nowLabel(): string {
  return new Date().toLocaleString('zh-CN', { hour12: false });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Tax 服务暂时不可用。';
}

export default function App() {
  const [currentTab, setCurrentTab] = useState<string>('dashboard');
  const [projectSubView, setProjectSubView] = useState<'list' | 'detail'>('list');
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [isAiOpen, setIsAiOpen] = useState<boolean>(true);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');

  const [systemSettings, setSystemSettings] = useState<SystemSettings>(() => {
    try {
      const saved = localStorage.getItem('ruibao_system_settings');
      return saved ? { ...DEFAULT_SETTINGS, ...JSON.parse(saved) } : { ...DEFAULT_SETTINGS };
    } catch {
      return { ...DEFAULT_SETTINGS };
    }
  });

  // 真实项目数据只能来自 Tax API。后端当前没有项目集合接口，必须由 VITE_PROJECT_IDS 显式提供待加载 ID。
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [projectStatus, setProjectStatus] = useState<DataStatus>('LOADING');
  const [projectStatusMessage, setProjectStatusMessage] = useState('正在从 Tax 服务加载项目数据…');
  const [aiModelStatus, setAiModelStatus] = useState<AiModelStatus>({
    state: 'LOADING',
    message: '正在读取 AI 模型状态…',
    endpoints: [],
  });

  const [riskEvents, setRiskEvents] = useState<RiskEvent[]>([]);
  const [riskStatus, setRiskStatus] = useState<DataStatus>('LOADING');
  const [riskStatusMessage, setRiskStatusMessage] = useState('正在从 Tax 服务加载风险集合…');
  const [taxLedgerStatus, setTaxLedgerStatus] = useState<DataStatus>('LOADING');
  const [taxLedgerStatusMessage, setTaxLedgerStatusMessage] = useState('正在从 Tax 服务加载台账集合…');
  const [isLedgerRebuilding, setIsLedgerRebuilding] = useState(false);
  const [auditLogs, setAuditLogs] = useState<AuditTrailRecord[]>([]);
  const [auditStatus, setAuditStatus] = useState<DataStatus>('LOADING');
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const [isAiThinking, setIsAiThinking] = useState<boolean>(false);
  const [actionNotice, setActionNotice] = useState<string>('');
  const projectAbortRef = useRef<AbortController | null>(null);
  const assistantAbortRef = useRef<AbortController | null>(null);

  const [isNewRecordModalOpen, setIsNewRecordModalOpen] = useState<boolean>(false);
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);

  const loadProjects = useCallback(async (signal?: AbortSignal) => {
    const ownedController = signal ? null : new AbortController();
    if (ownedController) {
      projectAbortRef.current?.abort();
      projectAbortRef.current = ownedController;
    }
    const requestSignal = signal ?? ownedController?.signal;
    setProjectStatus('LOADING');
    setProjectStatusMessage('正在从 Tax 服务加载项目数据…');
    setRiskStatus('LOADING');
    setRiskStatusMessage('正在从 Tax 服务加载风险集合…');
    setTaxLedgerStatus('LOADING');
    setTaxLedgerStatusMessage('正在从 Tax 服务加载台账集合…');
    setAuditStatus('LOADING');
    try {
      const loadedProjects = await fetchConfiguredProjects(requestSignal);
      // Project summaries, the deterministic Tax ledger, and risk events have
      // separate contracts. Each collection is retained independently so one
      // unavailable project does not fabricate or hide another project's data.
      const [ledgerResults, riskResults, auditResult] = await Promise.all([
        Promise.allSettled(loadedProjects.map(project => fetchTaxLedger(project.numericId, requestSignal))),
        Promise.allSettled(loadedProjects.map(project => fetchRiskEvents(project.numericId, requestSignal))),
        fetchAuditLogs(requestSignal).catch(() => ({ items: [], status: 'READY' as DataStatus, message: '' })),
      ]);
      if (requestSignal?.aborted) return;
      const ledgerUnavailable = ledgerResults.some(result => result.status === 'rejected');
      const ledgerFulfilled = ledgerResults.filter(result => result.status === 'fulfilled');
      const ledgerBackendDegraded = ledgerResults.some(result => result.status === 'fulfilled' && result.value.status !== 'READY');
      const ledgerAllFailed = ledgerFulfilled.length === 0;
      const projectsWithLedger = loadedProjects.map((project, index) => {
        const ledger = ledgerResults[index];
        return ledger?.status === 'fulfilled'
          ? { ...project, taxRecords: ledger.value.items }
          : project;
      });
      const successfulRiskResults = riskResults.filter(result => result.status === 'fulfilled');
      const riskRequestFailed = riskResults.some(result => result.status === 'rejected');
      const riskAllFailed = successfulRiskResults.length === 0;
      const riskBackendDegraded = riskResults.some(result => result.status === 'fulfilled' && result.value.status !== 'READY');
      const loadedRiskEvents = riskResults.flatMap(result => result.status === 'fulfilled' ? result.value.items : []);
      setProjects(projectsWithLedger);
      setSelectedProjectId(current => projectsWithLedger.some(project => project.id === current) ? current : projectsWithLedger[0].id);
      setRiskEvents(loadedRiskEvents);
      if (auditResult) {
        setAuditLogs(auditResult.items);
        setAuditStatus(auditResult.status);
      }
      const nextRiskStatus: DataStatus = riskAllFailed
        ? 'UNAVAILABLE'
        : riskRequestFailed || riskBackendDegraded ? 'DEGRADED' : 'READY';
      setRiskStatus(nextRiskStatus);
      const riskMessages = riskResults
        .filter((result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof fetchRiskEvents>>> => result.status === 'fulfilled')
        .map(result => result.value.message.trim())
        .filter(Boolean);
      setRiskStatusMessage(
        nextRiskStatus === 'UNAVAILABLE'
          ? '风险集合请求全部失败，未使用本地数据填充。'
          : riskMessages[0] || (loadedRiskEvents.length > 0 ? `已加载 ${loadedRiskEvents.length} 条真实风险事件。` : '暂无已识别风险事件。'),
      );
      const nextLedgerStatus: DataStatus = ledgerAllFailed
        ? 'UNAVAILABLE'
        : ledgerUnavailable || ledgerBackendDegraded ? 'DEGRADED' : 'READY';
      setTaxLedgerStatus(nextLedgerStatus);
      const ledgerMessages = ledgerResults
        .filter((result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof fetchTaxLedger>>> => result.status === 'fulfilled')
        .map(result => result.value.message.trim())
        .filter(Boolean);
      setTaxLedgerStatusMessage(
        nextLedgerStatus === 'UNAVAILABLE'
          ? '台账集合请求全部失败，未使用本地数据填充。'
          : ledgerMessages[0] || (nextLedgerStatus === 'DEGRADED' ? '部分台账集合数据不可用。' : '已加载 Tax API 台账集合数据。'),
      );
      setProjectStatus('READY');
      setProjectStatusMessage(
        ledgerUnavailable || ledgerBackendDegraded
          ? `已加载 ${projectsWithLedger.length} 个项目；部分 Tax 台账接口暂不可用，未使用本地数据填充。`
          : `已加载 ${projectsWithLedger.length} 个项目及其 Tax API 台账数据。`,
      );
    } catch (error) {
      if (requestSignal?.aborted) return;
      setProjects([]);
      setRiskEvents([]);
      setRiskStatus('UNAVAILABLE');
      setRiskStatusMessage('项目数据不可用，未读取风险集合。');
      setTaxLedgerStatus('UNAVAILABLE');
      setTaxLedgerStatusMessage('项目数据不可用，未读取台账集合。');
      setProjectStatus(error instanceof ApiError && error.status > 0 ? 'DEGRADED' : 'UNAVAILABLE');
      setProjectStatusMessage(errorMessage(error));
    } finally {
      if (ownedController && projectAbortRef.current === ownedController) projectAbortRef.current = null;
    }
  }, []);

  useEffect(() => {
    void loadProjects();
    return () => projectAbortRef.current?.abort();
  }, [loadProjects]);

  useEffect(() => {
    const controller = new AbortController();
    setAiModelStatus({ state: 'LOADING', message: '正在读取 AI 模型状态…', endpoints: [] });
    void fetchAiModelStatus(controller.signal)
      .then(status => {
        if (!controller.signal.aborted) setAiModelStatus(status);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          // A failed health request is not evidence that a model is loaded.
          setAiModelStatus({ state: 'UNAVAILABLE', message: '状态暂不可用', endpoints: [] });
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    projectAbortRef.current?.abort();
    assistantAbortRef.current?.abort();
  }, []);

  const currentProject = projects.find(project => project.id === selectedProjectId);
  const unresolvedRiskCount = riskEvents.filter(risk => risk.status !== '已闭环').length;

  const handleSaveSettings = (newSettings: SystemSettings) => {
    setSystemSettings(newSettings);
    try {
      localStorage.setItem('ruibao_system_settings', JSON.stringify(newSettings));
    } catch {
      setActionNotice('本机浏览器不允许保存设置；本次设置只在当前页面生效。');
    }
    setActionNotice('Tax 风控参数已应用；RAG 地址由管理员测试后保存至 Tax 后端。');
  };

  const handleSelectTab = (tab: string) => {
    setCurrentTab(tab);
    if (tab === 'projects') setProjectSubView('list');
    setIsMobileMenuOpen(false);
  };

  const handleSelectProject = (projectId: string) => {
    if (!projects.some(project => project.id === projectId)) {
      setActionNotice('该项目不在当前 Tax API 返回结果中，未执行页面切换。');
      return;
    }
    setSelectedProjectId(projectId);
    setCurrentTab('projects');
    setProjectSubView('detail');
    setIsMobileMenuOpen(false);
  };

  const handleResolveRisk = () => {
    setActionNotice('风险处置接口尚未接通，未在浏览器本地修改风险状态。');
  };

  const handleRagSyncCompleted = () => {
    setActionNotice('RAG 同步已完成，正在重新读取 Tax 项目与确定性台账数据。');
    void loadProjects();
  };

  const handleRebuildTaxLedger = async (period: string) => {
    if (isLedgerRebuilding) return;
    setIsLedgerRebuilding(true);
    try {
      const result = await rebuildTaxLedger(period);
      await loadProjects();
      setActionNotice(`已生成 ${result.rowCount} 条 ${result.period} 台账，页面已重新读取 Tax 项目与确定性台账数据。`);
    } catch (error) {
      setActionNotice(`台账生成/重建失败：${errorMessage(error)}；已保留当前页面最后可信数据。`);
    } finally {
      setIsLedgerRebuilding(false);
    }
  };

  const handleSendAiMessage = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed || isAiThinking) return;
    const userMessage: AssistantMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      content: trimmed,
      timestamp: nowLabel(),
    };
    setAssistantMessages(previous => [...previous, userMessage]);

    if (!currentProject) {
      setAssistantMessages(previous => [...previous, {
        id: `system-${Date.now()}`,
        sender: 'system',
        content: 'AI 问答不可用：当前没有已加载的真实项目数据。',
        timestamp: nowLabel(),
      }]);
      return;
    }

    assistantAbortRef.current?.abort();
    const controller = new AbortController();
    assistantAbortRef.current = controller;
    setIsAiThinking(true);
    try {
      const answer = await askProjectAi(currentProject.numericId, trimmed, undefined, controller.signal);
      setAssistantMessages(previous => [...previous, {
        id: `ai-${Date.now()}`,
        sender: 'ai',
        content: answer.answer,
        timestamp: nowLabel(),
        aiMetadata: answer.metadata,
      }]);
    } catch (error) {
      if (!controller.signal.aborted) {
        setAssistantMessages(previous => [...previous, {
          id: `system-${Date.now()}`,
          sender: 'system',
          content: `AI 问答 DEGRADED：${errorMessage(error)}`,
          timestamp: nowLabel(),
        }]);
      }
    } finally {
      if (!controller.signal.aborted) setIsAiThinking(false);
    }
  };

  const handleAskAiAboutRisk = (entityName: string) => {
    setIsAiOpen(true);
    void handleSendAiMessage(`请基于真实项目数据核查 ${entityName} 的涉税风险，并列出需要补充的证据。`);
  };

  return (
    <div className="fixed inset-0 h-screen w-screen bg-[#0b1326] text-[#dae2fd] flex flex-col md:flex-row  overflow-hidden font-sans">
      <Sidebar currentTab={currentTab} onSelectTab={handleSelectTab} unresolvedRiskCount={unresolvedRiskCount} aiModelStatus={aiModelStatus} />

      {isMobileMenuOpen && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          <div className="fixed inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
          <div className="relative w-64 h-full bg-[#0b1326] z-10 flex flex-col shadow-2xl">
            <Sidebar isMobile onCloseMobile={() => setIsMobileMenuOpen(false)} currentTab={currentTab} onSelectTab={handleSelectTab} unresolvedRiskCount={unresolvedRiskCount} aiModelStatus={aiModelStatus} />
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-col md:ml-48 min-w-0 h-full overflow-hidden relative">
        <Header
          onToggleAi={() => setIsAiOpen(open => !open)}
          isAiOpen={isAiOpen}
          onOpenExportModal={() => setIsExportModalOpen(true)}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onToggleMobileMenu={() => setIsMobileMenuOpen(open => !open)}
          settings={systemSettings}
          onSaveSettings={handleSaveSettings}
          riskStatus={riskStatus}
          unresolvedRiskCount={unresolvedRiskCount}
        />

        <div className="flex-1 flex pt-16 h-full w-full overflow-hidden">
          <main className="flex-1 min-w-0 h-full overflow-y-auto overscroll-contain p-3.5 md:p-5 lg:p-6 scrollbar-hide bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-[#171f33]/40 via-[#0b1326] to-[#0b1326]">
            <div className="w-full mx-auto pb-16">
              {actionNotice && (
                <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-[#F59E0B]/30 bg-[#F59E0B]/5 px-3 py-2 text-[12px] text-[#ffd0a8]" role="status">
                  <span>{actionNotice}</span>
                  <button type="button" onClick={() => setActionNotice('')} className="text-[#8e909f] hover:text-[#dae2fd]">关闭</button>
                </div>
              )}

              {currentTab === 'dashboard' && (
                <DashboardView
                  projects={projects}
                  dataStatus={projectStatus}
                  dataStatusMessage={projectStatusMessage}
                  onRetry={() => void loadProjects()}
                  onSelectProject={handleSelectProject}
                  onOpenRiskCenter={() => setCurrentTab('risk-center')}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  riskEvents={riskEvents}
                  riskStatus={riskStatus}
                  riskStatusMessage={riskStatusMessage}
                  taxLedgerStatus={taxLedgerStatus}
                  taxLedgerStatusMessage={taxLedgerStatusMessage}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'list' && (
                <ProjectRepositoryView
                  projects={projects}
                  dataStatus={projectStatus}
                  dataStatusMessage={projectStatusMessage}
                  onRetry={() => void loadProjects()}
                  onSelectProject={handleSelectProject}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onNavigateToAiReview={projectId => { setSelectedProjectId(projectId); setCurrentTab('ai-review'); }}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'detail' && currentProject && (
                <ProjectDetailView
                  project={currentProject}
                  projects={projects}
                  onSelectProject={projectId => setSelectedProjectId(projectId)}
                  onBack={() => setProjectSubView('list')}
                  onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)}
                  onOpenExportModal={() => setIsExportModalOpen(true)}
                  onAskAiAboutRisk={handleAskAiAboutRisk}
                  onGoToPlanning={() => setCurrentTab('tax-planning')}
                  onProjectDataDeleted={() => void loadProjects()}
                  settings={systemSettings}
                />
              )}

              {currentTab === 'projects' && projectSubView === 'detail' && !currentProject && (
                <DataStatusCard status={projectStatus} title="项目详情不可用" message={projectStatusMessage} onRetry={() => void loadProjects()} />
              )}

              {currentTab === 'tax-ledger' && (
                <TaxLedgerView projects={projects} dataStatus={taxLedgerStatus} dataStatusMessage={taxLedgerStatusMessage} onRetry={() => void loadProjects()} onOpenNewRecordModal={() => setIsNewRecordModalOpen(true)} onOpenExportModal={() => setIsExportModalOpen(true)} onAskAiAboutRisk={handleAskAiAboutRisk} onRebuildTaxLedger={handleRebuildTaxLedger} isRebuilding={isLedgerRebuilding} settings={systemSettings} />
              )}
              {currentTab === 'tax-planning' && (
                <TaxPlanningView projects={projects} selectedProjectId={selectedProjectId} onSelectProject={setSelectedProjectId} onAskAiAboutRisk={handleAskAiAboutRisk} />
              )}
              {currentTab === 'risk-center' && (
                <RiskCenterView riskEvents={riskEvents} dataStatus={riskStatus} dataStatusMessage={riskStatusMessage} onResolveRisk={handleResolveRisk} onAskAiAboutRisk={handleAskAiAboutRisk} settings={systemSettings} />
              )}
              {currentTab === 'ai-review' && <AiReviewView projects={projects} dataStatus={projectStatus} onAskAiAboutRisk={handleAskAiAboutRisk} />}
              {currentTab === 'audit' && <AuditView auditLogs={auditLogs} dataStatus={auditStatus} onOpenExportModal={() => setIsExportModalOpen(true)} />}
            </div>
          </main>

          <AiAssistantDrawer isOpen={isAiOpen} onClose={() => setIsAiOpen(false)} messages={assistantMessages} onSendMessage={query => void handleSendAiMessage(query)} isAiThinking={isAiThinking} settings={systemSettings} />
        </div>
      </div>

      {isNewRecordModalOpen && (
        <NewTaxRecordModal
          isOpen
          onClose={() => setIsNewRecordModalOpen(false)}
          projects={projects}
          projectId={currentProject?.numericId}
          defaultProjectCode={currentProject?.projectCode}
          defaultProjectName={currentProject?.name}
          onSyncCompleted={handleRagSyncCompleted}
        />
      )}
      <ExportReportModal isOpen={isExportModalOpen} onClose={() => setIsExportModalOpen(false)} projects={projects} />
    </div>
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
